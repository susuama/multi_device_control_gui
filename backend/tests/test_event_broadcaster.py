"""スレッド間のイベント配信と、購読キューの上限・終了通知を確認する。"""

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
            self.broadcaster.publish(event)

        for subscriber in (first, second):
            received = [await asyncio.wait_for(subscriber.get(), 1) for _ in events]
            self.assertEqual(events, received)

    async def test_publish_from_worker_wakes_waiting_subscriber(self):
        subscriber = self.broadcaster.subscribe()
        event = {"type": "sample", "data": {"power_w": 0.001}}
        receiver = asyncio.create_task(subscriber.get())
        await asyncio.sleep(0)

        await asyncio.to_thread(self.broadcaster.publish, event)

        self.assertEqual(event, await asyncio.wait_for(receiver, 1))

    async def test_unsubscribe_stops_delivery_and_can_be_repeated(self):
        removed = self.broadcaster.subscribe()
        remaining = self.broadcaster.subscribe()
        self.broadcaster.unsubscribe(removed)
        self.broadcaster.unsubscribe(removed)
        event = {"type": "sample"}

        self.broadcaster.publish(event)

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
            self.broadcaster.publish(event)
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
        self.broadcaster.publish(event)
        await asyncio.sleep(0)

        self.broadcaster.shutdown()

        self.assertEqual(event, subscriber.get_nowait())
        self.assertIsNone(subscriber.get_nowait())
        self.assertTrue(subscriber.empty())

    async def test_shutdown_can_notify_full_queue(self):
        subscriber = self.broadcaster.subscribe()
        for value in range(100):
            self.broadcaster.publish({"value": value})
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
        self.broadcaster.publish({"value": "pending"})

        self.broadcaster.shutdown()
        self.broadcaster.publish({"value": "later"})
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
        self.broadcaster.publish(event)
        self.assertEqual(event, await asyncio.wait_for(subscriber.get(), 1))

    async def test_start_after_shutdown_fails(self):
        self.broadcaster.shutdown()
        with self.assertRaisesRegex(RuntimeError, "shut down"):
            self.broadcaster.start(asyncio.get_running_loop())

    async def test_publish_without_subscribers_is_allowed(self):
        self.broadcaster.publish({"value": 1})
        await asyncio.sleep(0)
        subscriber = self.broadcaster.subscribe()
        self.assertTrue(subscriber.empty())


class EventBroadcasterLifecycleTests(unittest.TestCase):
    def test_subscribe_before_start_fails(self):
        broadcaster = EventBroadcaster()
        with self.assertRaisesRegex(RuntimeError, "not running"):
            broadcaster.subscribe()

    def test_publish_before_start_and_after_loop_close_is_ignored(self):
        broadcaster = EventBroadcaster()
        broadcaster.publish({"value": "before"})
        loop = asyncio.new_event_loop()
        try:
            broadcaster.start(loop)
            broadcaster.shutdown()
        finally:
            loop.close()
        broadcaster.publish({"value": "after"})


if __name__ == "__main__":
    unittest.main()
