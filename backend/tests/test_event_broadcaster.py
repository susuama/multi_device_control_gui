"""キー別のイベント配信と、購読キューの上限・終了通知を確認する。"""

import asyncio
import unittest

from backend.app.services.event_broadcaster import EventBroadcaster


class EventBroadcasterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broadcaster = EventBroadcaster()
        self.broadcaster.start(asyncio.get_running_loop())

    async def asyncTearDown(self):
        self.broadcaster.shutdown()

    async def test_publish_delivers_to_each_subscriber_in_order(self):
        first = self.broadcaster.subscribe()
        second = self.broadcaster.subscribe()
        self.assertIsNot(first, second)
        events = [{"type": "sample", "value": value} for value in range(3)]

        for event in events:
            self.broadcaster.publish(event, key=None)

        for subscriber in (first, second):
            received = [await asyncio.wait_for(subscriber.get(), 1) for _ in events]
            self.assertEqual(events, received)

    async def test_publish_from_worker_wakes_waiting_subscriber(self):
        subscriber = self.broadcaster.subscribe()
        event = {"type": "sample", "data": {"power_w": 0.001}}
        receiver = asyncio.create_task(subscriber.get())
        await asyncio.sleep(0)

        await asyncio.to_thread(self.broadcaster.publish, event, key=None)

        self.assertEqual(event, await asyncio.wait_for(receiver, 1))

    async def test_unsubscribe_stops_delivery_and_can_be_repeated(self):
        removed = self.broadcaster.subscribe()
        remaining = self.broadcaster.subscribe()
        self.broadcaster.unsubscribe(removed)
        self.broadcaster.unsubscribe(removed)
        event = {"type": "sample"}

        self.broadcaster.publish(event, key=None)

        self.assertEqual(event, await asyncio.wait_for(remaining.get(), 1))
        self.assertTrue(removed.empty())
        self.broadcaster.shutdown()
        self.assertTrue(removed.empty())

    async def test_slow_subscriber_drops_oldest_without_affecting_fast_subscriber(self):
        slow = self.broadcaster.subscribe()
        fast = self.broadcaster.subscribe()
        self.assertEqual(100, slow.maxsize)

        for value in range(105):
            event = {"value": value}
            self.broadcaster.publish(event, key=None)
            self.assertEqual(event, await asyncio.wait_for(fast.get(), 1))

        self.assertEqual(100, slow.qsize())
        received = []
        while not slow.empty():
            received.append(slow.get_nowait()["value"])
            slow.task_done()
        self.assertEqual(list(range(5, 105)), received)
        await asyncio.wait_for(slow.join(), 1)

    async def test_shutdown_wakes_all_waiting_subscribers(self):
        receivers = [asyncio.create_task(self.broadcaster.subscribe().get()) for _ in range(2)]
        await asyncio.sleep(0)

        self.broadcaster.shutdown()

        self.assertEqual([None, None], await asyncio.wait_for(asyncio.gather(*receivers), 1))

    async def test_shutdown_preserves_queued_events_before_end_notification(self):
        subscriber = self.broadcaster.subscribe()
        event = {"value": 1}
        self.broadcaster.publish(event, key=None)
        await asyncio.sleep(0)

        self.broadcaster.shutdown()

        self.assertEqual(event, subscriber.get_nowait())
        self.assertIsNone(subscriber.get_nowait())
        self.assertTrue(subscriber.empty())

    async def test_shutdown_can_notify_full_queue(self):
        subscriber = self.broadcaster.subscribe()
        for value in range(100):
            self.broadcaster.publish({"value": value}, key=None)
        await asyncio.sleep(0)

        self.broadcaster.shutdown()

        self.assertEqual(100, subscriber.qsize())
        received = [subscriber.get_nowait() for _ in range(100)]
        self.assertEqual([{"value": value} for value in range(1, 100)], received[:-1])
        self.assertIsNone(received[-1])
        for _ in received:
            subscriber.task_done()
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_shutdown_ignores_pending_and_later_publications(self):
        subscriber = self.broadcaster.subscribe()
        self.broadcaster.publish({"value": "pending"}, key=None)

        self.broadcaster.shutdown()
        self.broadcaster.publish({"value": "later"}, key=None)
        await asyncio.sleep(0)

        self.assertIsNone(subscriber.get_nowait())
        self.assertTrue(subscriber.empty())

    async def test_repeated_shutdown_does_not_add_another_notification(self):
        subscriber = self.broadcaster.subscribe()

        self.broadcaster.shutdown()
        self.broadcaster.shutdown()

        self.assertIsNone(subscriber.get_nowait())
        self.assertTrue(subscriber.empty())

    async def test_subscribe_after_shutdown_fails(self):
        self.broadcaster.shutdown()
        with self.assertRaisesRegex(RuntimeError, "not running"):
            self.broadcaster.subscribe()

    async def test_start_rejects_loop_replacement(self):
        subscriber = self.broadcaster.subscribe()
        with self.assertRaisesRegex(RuntimeError, "already been started"):
            self.broadcaster.start(asyncio.get_running_loop())

        event = {"value": 1}
        self.broadcaster.publish(event, key=None)
        self.assertEqual(event, await asyncio.wait_for(subscriber.get(), 1))

    async def test_start_after_shutdown_fails(self):
        self.broadcaster.shutdown()
        with self.assertRaisesRegex(RuntimeError, "shut down"):
            self.broadcaster.start(asyncio.get_running_loop())

    async def test_publish_without_subscribers_is_allowed(self):
        self.broadcaster.publish({"value": 1}, key=None)
        await asyncio.sleep(0)
        subscriber = self.broadcaster.subscribe()
        self.assertTrue(subscriber.empty())

    async def test_keys_match_exactly_and_each_matching_subscriber_receives_event(self):
        subscribers = [
            (None, self.broadcaster.subscribe()),
            ("camera-A", self.broadcaster.subscribe(key="camera-A")),
            ("camera-A", self.broadcaster.subscribe(key="camera-A")),
            ("camera-B", self.broadcaster.subscribe(key="camera-B")),
        ]
        for key in (None, "camera-A", "camera-B", "unknown"):
            self.broadcaster.publish({"source": key}, key=key)
        await asyncio.sleep(0)

        for key, subscriber in subscribers:
            self.assertEqual(1, subscriber.qsize())
            self.assertEqual({"source": key}, subscriber.get_nowait())
            subscriber.task_done()
            await asyncio.wait_for(subscriber.join(), 1)

    async def test_queue_limits_are_independent_for_same_key(self):
        latest = self.broadcaster.subscribe(key="camera", maxsize=1)
        history = self.broadcaster.subscribe(key="camera", maxsize=100)
        other = self.broadcaster.subscribe(key="other", maxsize=1)
        for value in range(3):
            self.broadcaster.publish({"value": value}, key="camera")
        await asyncio.sleep(0)

        self.assertEqual(1, latest.qsize())
        self.assertEqual({"value": 2}, latest.get_nowait())
        latest.task_done()
        self.assertEqual([{"value": value} for value in range(3)],
                         [history.get_nowait() for _ in range(3)])
        for _ in range(3):
            history.task_done()
        await asyncio.wait_for(asyncio.gather(latest.join(), history.join()), 1)
        self.assertTrue(other.empty())

    async def test_nonmatching_publication_does_not_replace_queued_image(self):
        image_queue = self.broadcaster.subscribe(key="camera", maxsize=1)
        image = {"jpeg": b"image"}
        self.broadcaster.publish(image, key="camera")
        self.broadcaster.publish({"value": 1}, key=None)
        self.broadcaster.publish({"jpeg": b"other"}, key="other-camera")
        await asyncio.sleep(0)

        self.assertIs(image, image_queue.get_nowait())
        image_queue.task_done()
        await asyncio.wait_for(image_queue.join(), 1)

    async def test_binary_payload_is_shared_with_matching_subscribers_from_worker(self):
        first = self.broadcaster.subscribe(key="camera", maxsize=1)
        second = self.broadcaster.subscribe(key="camera", maxsize=1)
        event = {"jpeg": b"\xff\xd8\xff\xd9", "frame_index": 7}
        receiver = asyncio.create_task(first.get())
        await asyncio.sleep(0)

        await asyncio.to_thread(self.broadcaster.publish, event, key="camera")

        self.assertIs(event, await asyncio.wait_for(receiver, 1))
        self.assertIs(event, second.get_nowait())
        for subscriber in (first, second):
            subscriber.task_done()
            await asyncio.wait_for(subscriber.join(), 1)

    async def test_invalid_queue_limit_is_rejected_without_registering(self):
        for maxsize in (0, -1, 1.5, "1", None, True, False):
            with self.subTest(maxsize=maxsize):
                error = ValueError if type(maxsize) is int else TypeError
                with self.assertRaises(error):
                    self.broadcaster.subscribe(maxsize=maxsize)
                self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_invalid_key_is_rejected_before_subscription_or_scheduling(self):
        subscriber = self.broadcaster.subscribe()
        for key in (1, True, [], {}):
            with self.subTest(key=key):
                with self.assertRaises(TypeError):
                    self.broadcaster.subscribe(key=key)
                with self.assertRaises(TypeError):
                    self.broadcaster.publish({"value": 1}, key=key)
        await asyncio.sleep(0)
        self.assertEqual(1, len(self.broadcaster._subscribers))
        self.assertTrue(subscriber.empty())

    async def test_publish_requires_explicit_key(self):
        with self.assertRaises(TypeError):
            self.broadcaster.publish({"value": 1})

    async def test_unsubscribe_removes_only_requested_queue_for_key(self):
        removed = self.broadcaster.subscribe(key="camera", maxsize=1)
        remaining = self.broadcaster.subscribe(key="camera", maxsize=1)
        self.broadcaster.unsubscribe(removed)
        self.broadcaster.unsubscribe(removed)
        self.broadcaster.publish({"value": 1}, key="camera")
        await asyncio.sleep(0)

        self.assertTrue(removed.empty())
        self.assertEqual({"value": 1}, remaining.get_nowait())
        remaining.task_done()
        await asyncio.wait_for(remaining.join(), 1)

    async def test_shutdown_notifies_full_queues_for_all_keys(self):
        subscribers = [
            self.broadcaster.subscribe(key=None, maxsize=100),
            self.broadcaster.subscribe(key="camera-A", maxsize=1),
            self.broadcaster.subscribe(key="camera-B", maxsize=1),
        ]
        for value in range(100):
            self.broadcaster.publish({"value": value}, key=None)
        for key in ("camera-A", "camera-B"):
            self.broadcaster.publish({"jpeg": b"image"}, key=key)
        await asyncio.sleep(0)

        self.broadcaster.shutdown()
        self.broadcaster.shutdown()

        for subscriber in subscribers:
            received = [subscriber.get_nowait() for _ in range(subscriber.qsize())]
            self.assertIsNone(received[-1])
            self.assertEqual(1, received.count(None))
            for _ in received:
                subscriber.task_done()
            await asyncio.wait_for(subscriber.join(), 1)
        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_shutdown_wakes_keyed_receivers_and_ignores_pending_images(self):
        waiting = self.broadcaster.subscribe(key="camera-A", maxsize=1)
        pending = self.broadcaster.subscribe(key="camera-B", maxsize=1)
        receiver = asyncio.create_task(waiting.get())
        await asyncio.sleep(0)
        self.broadcaster.publish({"jpeg": b"pending"}, key="camera-B")

        self.broadcaster.shutdown()
        self.broadcaster.publish({"jpeg": b"later"}, key="camera-A")
        await asyncio.sleep(0)

        self.assertIsNone(await asyncio.wait_for(receiver, 1))
        self.assertIsNone(pending.get_nowait())
        for subscriber in (waiting, pending):
            self.assertTrue(subscriber.empty())
            subscriber.task_done()
            await asyncio.wait_for(subscriber.join(), 1)


class EventBroadcasterLifecycleTests(unittest.TestCase):
    def test_subscribe_before_start_fails(self):
        broadcaster = EventBroadcaster()
        with self.assertRaisesRegex(RuntimeError, "not running"):
            broadcaster.subscribe()

    def test_publish_before_start_and_after_loop_close_is_ignored(self):
        broadcaster = EventBroadcaster()
        broadcaster.publish({"value": "before"}, key=None)
        loop = asyncio.new_event_loop()
        try:
            broadcaster.start(loop)
            broadcaster.shutdown()
        finally:
            loop.close()
        broadcaster.publish({"value": "after"}, key=None)


if __name__ == "__main__":
    unittest.main()
