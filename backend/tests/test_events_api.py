"""WebSocketルートの配信、切断、終了通知とタスクの後処理を確認する。"""

import asyncio
import csv
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, WebSocket

from backend.app.api.events import router
from backend.app.main import create_app
from backend.app.models.events import MeasurementEvent
from backend.app.services.event_broadcaster import EventBroadcaster


class EventsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        logging.disable(logging.CRITICAL)
        self.broadcaster = EventBroadcaster()
        self.broadcaster.start(asyncio.get_running_loop())
        self.app = FastAPI()
        self.app.state.event_broadcaster = self.broadcaster
        self.app.include_router(router)
        self.connections = []
        self.send_error = None

    async def asyncTearDown(self):
        self.broadcaster.shutdown()
        for task in self.connections:
            task.cancel()
        await asyncio.gather(*self.connections, return_exceptions=True)

    async def connect(self):
        """ASGIの入出力をキューへ接続し、WebSocketの接続受付を待つ。"""
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()

        async def send(message):
            if message["type"] == "websocket.send" and self.send_error is not None:
                raise self.send_error
            await outgoing.put(message)

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "path": "/ws",
            "raw_path": b"/ws",
            "root_path": "",
            "scheme": "ws",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "subprotocols": [],
        }
        task = asyncio.create_task(self.app(scope, incoming.get, send))
        self.connections.append(task)
        await incoming.put({"type": "websocket.connect"})
        accepted = await asyncio.wait_for(outgoing.get(), 1)
        self.assertEqual("websocket.accept", accepted["type"])
        return task, incoming, outgoing

    async def test_receives_multiple_events_and_completes_queue_tasks(self):
        _, _, outgoing = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))
        events = [{"type": "sample", "data": {"power_w": value}} for value in (0.001, 0.002)]

        for event in events:
            self.broadcaster.publish(event)
            message = await asyncio.wait_for(outgoing.get(), 1)
            self.assertEqual("websocket.send", message["type"])
            self.assertEqual(event, json.loads(message["text"]))
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_idle_disconnect_unregisters_without_waiting_for_event(self):
        task, incoming, _ = await self.connect()

        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 1)

        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_client_messages_do_not_stop_event_delivery(self):
        _, incoming, outgoing = await self.connect()
        await incoming.put({"type": "websocket.receive", "text": "ignored"})
        await incoming.put({"type": "websocket.receive", "bytes": b"ignored"})
        event = {"type": "sample", "data": {"power_w": 0.001}}

        self.broadcaster.publish(event)

        message = await asyncio.wait_for(outgoing.get(), 1)
        self.assertEqual(event, json.loads(message["text"]))

    async def test_shutdown_closes_connection_and_completes_end_notification(self):
        task, _, outgoing = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))

        self.broadcaster.shutdown()

        message = await asyncio.wait_for(outgoing.get(), 1)
        self.assertEqual("websocket.close", message["type"])
        self.assertEqual(1000, message["code"])
        await asyncio.wait_for(task, 1)
        await asyncio.wait_for(subscriber.join(), 1)
        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_disconnect_does_not_stop_other_connection(self):
        first, first_incoming, _ = await self.connect()
        _, _, second_outgoing = await self.connect()
        await first_incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(first, 1)
        self.assertEqual(1, len(self.broadcaster._subscribers))

        event = {"value": 1}
        self.broadcaster.publish(event)
        message = await asyncio.wait_for(second_outgoing.get(), 1)
        self.assertEqual(event, json.loads(message["text"]))

    async def test_transport_disconnect_during_send_is_handled(self):
        task, _, _ = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))
        self.send_error = OSError("Connection closed")

        self.broadcaster.publish({"value": 1})
        await asyncio.wait_for(task, 1)

        self.assertEqual(0, len(self.broadcaster._subscribers))
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_unexpected_send_error_is_preserved_after_cleanup(self):
        task, _, _ = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))
        self.send_error = RuntimeError("send failed")

        self.broadcaster.publish({"value": 1})
        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await asyncio.wait_for(task, 1)

        self.assertEqual(0, len(self.broadcaster._subscribers))
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_disconnect_cancels_blocked_send_and_completes_queue_task(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked_send(websocket, event):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(WebSocket, "send_json", new=blocked_send):
            task, incoming, _ = await self.connect()
            subscriber = next(iter(self.broadcaster._subscribers))
            self.broadcaster.publish({"value": 1})
            await asyncio.wait_for(entered.wait(), 1)

            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 1)

        self.assertTrue(cancelled.is_set())
        self.assertEqual(0, len(self.broadcaster._subscribers))
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_endpoint_cancellation_unregisters_subscription(self):
        task, _, _ = await self.connect()

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_main_app_lifespan_starts_broadcaster_and_registers_route(self):
        self.app = create_app()
        async with self.app.router.lifespan_context(self.app):
            self.broadcaster = self.app.state.event_broadcaster
            task, incoming, outgoing = await self.connect()
            event = {"value": 1}
            self.broadcaster.publish(event)
            message = await asyncio.wait_for(outgoing.get(), 1)
            self.assertEqual(event, json.loads(message["text"]))
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 1)

    async def test_dummy_devices_publish_measurements_without_recording(self):
        self.app = create_app()
        async with self.app.router.lifespan_context(self.app):
            self.broadcaster = self.app.state.event_broadcaster
            manager = self.app.state.measurement_manager
            devices = self.app.state.device_manager
            devices.scan()
            device_ids = list(devices.get_all())
            self.assertEqual(2, len(device_ids))
            task, incoming, outgoing = await self.connect()

            for device_id in device_ids:
                await asyncio.to_thread(devices.connect, device_id)
                await asyncio.to_thread(manager.start_sampling, device_id)

            received = {}
            async with asyncio.timeout(3):
                while set(received) != set(device_ids):
                    message = await outgoing.get()
                    event = MeasurementEvent.model_validate_json(message["text"])
                    self.assertIn(event.id, device_ids)
                    self.assertIn("power_w", event.data)
                    self.assertEqual(manager._get_elapsed(event.timestamp_ns), event.elapsed_s)
                    self.assertEqual(manager._get_datetime(event.timestamp_ns), event.datetime)
                    received[event.id] = event

            self.assertFalse(manager.is_recording())
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 1)
            for device_id in device_ids:
                self.assertTrue(manager.is_sampling(device_id))

    async def test_measurement_delivery_and_csv_recording_work_together(self):
        self.app = create_app()
        with tempfile.TemporaryDirectory(prefix="events-recording-tests-") as directory:
            async with self.app.router.lifespan_context(self.app):
                self.broadcaster = self.app.state.event_broadcaster
                manager = self.app.state.measurement_manager
                devices = self.app.state.device_manager
                devices.scan()
                device_ids = list(devices.get_all())
                self.assertEqual(2, len(device_ids))
                task, incoming, outgoing = await self.connect()

                for device_id in device_ids:
                    await asyncio.to_thread(devices.connect, device_id)
                    await asyncio.to_thread(manager.start_sampling, device_id)
                await asyncio.to_thread(manager.start_recording, directory, "with_ws")

                while not outgoing.empty():
                    outgoing.get_nowait()
                received = {}
                async with asyncio.timeout(3):
                    while set(received) != set(device_ids):
                        message = await outgoing.get()
                        event = MeasurementEvent.model_validate_json(message["text"])
                        received[event.id] = event

                await incoming.put({"type": "websocket.disconnect", "code": 1000})
                await asyncio.wait_for(task, 1)
                self.assertTrue(manager.is_recording())
                for device_id in device_ids:
                    self.assertTrue(manager.is_sampling(device_id))
                await asyncio.to_thread(manager.stop_all_sampling)
                await asyncio.to_thread(manager.stop_recording)

                for device_id in device_ids:
                    path = Path(directory) / "with_ws" / f"{device_id}.csv"
                    with path.open(encoding="utf-8-sig", newline="") as file:
                        rows = list(csv.DictReader(file))
                    self.assertGreater(len(rows), 0)
                    self.assertTrue(all(row["id"] == device_id for row in rows))
                    self.assertIn(str(received[device_id].timestamp_ns), [row["timestamp_ns"] for row in rows])


if __name__ == "__main__":
    unittest.main()
