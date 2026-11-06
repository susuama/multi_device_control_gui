"""WebSocketルートの配信、切断、終了通知とタスクの後処理を確認する。"""

import asyncio
import base64
import csv
import json
import logging
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi import FastAPI, WebSocket
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from PIL import Image

from backend.app.api.events import router
from backend.app.main import create_app
from backend.app.models.events import CameraPreviewEvent, MeasurementEvent
from backend.app.services.event_broadcaster import EventBroadcaster


# 2×2画素のJPEG。送信テスト時に画像変換ライブラリを必要としない。
PREVIEW_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAgAAAQABAAD//gAPTGF2YzYxLjMuMTAwAP/bAEMACAQEBAQEBQUFBQUFBgYG"
    "BgYGBgYGBgYGBgcHBwgICAcHBwYGBwcICAgICQkJCAgICAkJCgoKDAwLCw4ODhERFP/EAEoAAQAA"
    "AAAAAAAAAAAAAAAAAAABAQAAAAAAAAAAAAAAAAAAAAAQAQAAAAAAAAAAAAAAAAAAAAARAQAAAAAAAAAA"
    "AAAAAAAAAAD/wAARCAACAAIDASIAAhEAAxEA/9oADAMBAAIRAxEAPwAAD//Z"
)


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

    async def open_connection(self, path):
        """指定パスのASGI接続を作り、受付または拒否のメッセージを返す。"""
        url = urlsplit(path)
        incoming = asyncio.Queue()
        outgoing = asyncio.Queue()

        async def send(message):
            if message["type"] == "websocket.send" and self.send_error is not None:
                raise self.send_error
            await outgoing.put(message)

        scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "path": url.path,
            "raw_path": url.path.encode("utf-8"),
            "root_path": "",
            "scheme": "ws",
            "query_string": url.query.encode("utf-8"),
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "subprotocols": [],
        }
        task = asyncio.create_task(self.app(scope, incoming.get, send))
        self.connections.append(task)
        await incoming.put({"type": "websocket.connect"})
        accepted = await asyncio.wait_for(outgoing.get(), 1)
        return task, incoming, outgoing, accepted

    async def connect(self, path="/ws"):
        """WebSocketの接続受付を確認し、タスクと入出力Queueを返す。"""
        task, incoming, outgoing, accepted = await self.open_connection(path)
        self.assertEqual("websocket.accept", accepted["type"])
        return task, incoming, outgoing

    def measurement_event(self, device_id="meter-A", power=0.001):
        """測定値と取得時刻を持つ、検証済みの配信用辞書を作成する。"""
        return MeasurementEvent(
            server_instance_id="server-test", measurement_id="period-test",
            id=device_id, timestamp_ns=123456789, elapsed_s=1.25,
            datetime=datetime.fromisoformat("2026-11-01T00:00:01.250000+09:00"),
            data={"power_w": power},
        ).model_dump(mode="json")

    def preview_event(self, device_id="camera-A", frame_index=7):
        """JPEGと対応する時刻・サイズを持つ配信用辞書を作成する。"""
        return CameraPreviewEvent(
            server_instance_id="server-test", measurement_id="period-test",
            id=device_id, frame_index=frame_index, timestamp_ns=10**18 + frame_index,
            width=2, height=2, jpeg=PREVIEW_JPEG,
        ).model_dump()

    def decode_preview(self, message):
        """バイナリメッセージを、フレーム情報とJPEGに分ける。"""
        self.assertEqual("websocket.send", message["type"])
        self.assertNotIn("text", message)
        payload = message["bytes"]
        header_length = int.from_bytes(payload[:4], "big")
        return json.loads(payload[4:4 + header_length]), payload[4 + header_length:]

    async def test_camera_preview_sends_metadata_and_jpeg_in_one_binary_message(self):
        _, _, outgoing = await self.connect("/ws/camera-A?maxsize=1")
        subscriber = next(iter(self.broadcaster._subscribers))
        self.assertEqual(1, subscriber.maxsize)
        self.assertEqual("camera-A", self.broadcaster._subscribers[subscriber])
        event = self.preview_event()

        self.broadcaster.publish(event, key="camera-A")

        metadata, jpeg = self.decode_preview(await asyncio.wait_for(outgoing.get(), 1))
        self.assertEqual({
            "type": "camera_preview", "id": "camera-A", "frame_index": 7,
            "server_instance_id": "server-test", "measurement_id": "period-test",
            "timestamp_ns": str(event["timestamp_ns"]), "width": 2, "height": 2,
        }, metadata)
        self.assertEqual(PREVIEW_JPEG, jpeg)
        self.assertTrue(outgoing.empty())
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_camera_and_numeric_connections_receive_only_their_keys(self):
        _, _, numeric = await self.connect()
        _, _, first = await self.connect("/ws/camera-A")
        _, _, second = await self.connect("/ws/camera-B")
        sample = self.measurement_event()

        self.broadcaster.publish(self.preview_event("camera-B", 2), key="camera-B")
        self.broadcaster.publish(sample, key=None)
        self.broadcaster.publish(self.preview_event("camera-A", 1), key="camera-A")

        self.assertEqual(sample, json.loads((await asyncio.wait_for(numeric.get(), 1))["text"]))
        for outgoing, device_id in ((first, "camera-A"), (second, "camera-B")):
            metadata, jpeg = self.decode_preview(await asyncio.wait_for(outgoing.get(), 1))
            self.assertEqual(device_id, metadata["id"])
            self.assertEqual(PREVIEW_JPEG, jpeg)
        for outgoing in (numeric, first, second):
            self.assertTrue(outgoing.empty())

    async def test_multiple_camera_subscribers_continue_after_one_disconnects(self):
        removed, incoming, _ = await self.connect("/ws/camera-A")
        _, _, first = await self.connect("/ws/camera-A")
        _, _, second = await self.connect("/ws/camera-A")
        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(removed, 1)

        self.broadcaster.publish(self.preview_event(), key="camera-A")

        first_message = await asyncio.wait_for(first.get(), 1)
        self.assertEqual(first_message, await asyncio.wait_for(second.get(), 1))
        self.assertEqual(2, len(self.broadcaster._subscribers))

    async def test_slow_camera_send_keeps_only_latest_waiting_frame(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        original_send = WebSocket.send_bytes

        async def delayed_send(websocket, data):
            if not entered.is_set():
                entered.set()
                await release.wait()
            await original_send(websocket, data)

        with patch.object(WebSocket, "send_bytes", new=delayed_send):
            _, _, outgoing = await self.connect("/ws/camera-A?maxsize=1")
            subscriber = next(iter(self.broadcaster._subscribers))
            self.broadcaster.publish(self.preview_event(frame_index=0), key="camera-A")
            await asyncio.wait_for(entered.wait(), 1)
            try:
                for index in (1, 2, 3):
                    self.broadcaster.publish(self.preview_event(frame_index=index), key="camera-A")
                await asyncio.sleep(0)
                self.assertEqual(1, subscriber.qsize())
            finally:
                release.set()

            for expected in (0, 3):
                metadata, _ = self.decode_preview(await asyncio.wait_for(outgoing.get(), 1))
                self.assertEqual(expected, metadata["frame_index"])
            self.assertTrue(outgoing.empty())
            await asyncio.wait_for(subscriber.join(), 1)

    async def test_slow_numeric_and_image_send_keep_the_original_ids(self):
        numeric_entered, image_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        send_json, send_bytes = WebSocket.send_json, WebSocket.send_bytes

        async def delayed_json(websocket, data, *args, **kwargs):
            if not numeric_entered.is_set():
                numeric_entered.set()
                await release.wait()
            await send_json(websocket, data, *args, **kwargs)

        async def delayed_bytes(websocket, data):
            if not image_entered.is_set():
                image_entered.set()
                await release.wait()
            await send_bytes(websocket, data)

        with patch.object(WebSocket, "send_json", new=delayed_json), \
                patch.object(WebSocket, "send_bytes", new=delayed_bytes):
            _, _, numeric = await self.connect()
            _, _, image = await self.connect("/ws/camera-A?maxsize=1")
            self.broadcaster.publish(self.measurement_event(), key=None)
            self.broadcaster.publish(self.preview_event(frame_index=0), key="camera-A")
            try:
                await asyncio.wait_for(numeric_entered.wait(), 1)
                await asyncio.wait_for(image_entered.wait(), 1)
                later_numeric = self.measurement_event() | {"server_instance_id": "server-next", "measurement_id": "period-next"}
                later_image = self.preview_event(frame_index=1) | {"server_instance_id": "server-next", "measurement_id": "period-next"}
                self.broadcaster.publish(later_numeric, key=None)
                self.broadcaster.publish(later_image, key="camera-A")
                await asyncio.sleep(0)
            finally:
                release.set()

            for server_id, period_id in (("server-test", "period-test"), ("server-next", "period-next")):
                number = json.loads((await asyncio.wait_for(numeric.get(), 1))["text"])
                metadata, _ = self.decode_preview(await asyncio.wait_for(image.get(), 1))
                for event in (number, metadata):
                    self.assertEqual(server_id, event["server_instance_id"])
                    self.assertEqual(period_id, event["measurement_id"])

    async def test_keyed_subscription_accepts_unregistered_key_and_sends_json(self):
        _, _, outgoing = await self.connect("/ws/meter-A")
        subscriber = next(iter(self.broadcaster._subscribers))
        self.assertEqual("meter-A", self.broadcaster._subscribers[subscriber])
        self.assertEqual(100, subscriber.maxsize)
        event = self.measurement_event()

        self.broadcaster.publish(event, key="meter-A")

        message = await asyncio.wait_for(outgoing.get(), 1)
        self.assertEqual(event, json.loads(message["text"]))
        self.assertNotIn("bytes", message)
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_subscription_key_comes_from_path_not_query(self):
        await self.connect("/ws?device_id=group")
        await self.connect("/ws/group?device_id=other")

        self.assertEqual([None, "group"], list(self.broadcaster._subscribers.values()))

    async def test_none_key_can_send_camera_preview(self):
        _, _, outgoing = await self.connect("/ws?maxsize=1")
        subscriber = next(iter(self.broadcaster._subscribers))
        self.assertIsNone(self.broadcaster._subscribers[subscriber])
        self.assertEqual(1, subscriber.maxsize)

        self.broadcaster.publish(self.preview_event(), key=None)

        metadata, jpeg = self.decode_preview(await asyncio.wait_for(outgoing.get(), 1))
        self.assertEqual("camera-A", metadata["id"])
        self.assertEqual(PREVIEW_JPEG, jpeg)
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_same_subscription_sends_json_and_binary_by_event_type(self):
        _, _, outgoing = await self.connect("/ws/group")
        sample = self.measurement_event()
        next_sample = self.measurement_event(power=0.002)

        self.broadcaster.publish(sample, key="group")
        self.broadcaster.publish(self.preview_event(), key="group")
        self.broadcaster.publish(next_sample, key="group")

        self.assertEqual(sample, json.loads((await asyncio.wait_for(outgoing.get(), 1))["text"]))
        metadata, jpeg = self.decode_preview(await asyncio.wait_for(outgoing.get(), 1))
        self.assertEqual("camera-A", metadata["id"])
        self.assertEqual(PREVIEW_JPEG, jpeg)
        self.assertEqual(next_sample, json.loads((await asyncio.wait_for(outgoing.get(), 1))["text"]))
        self.assertTrue(outgoing.empty())

    async def test_measurement_is_validated_and_converted_to_json_before_sending(self):
        for key, path in ((None, "/ws"), ("meter-A", "/ws/meter-A")):
            with self.subTest(key=key):
                task, incoming, outgoing = await self.connect(path)
                expected = self.measurement_event()
                event = dict(expected)
                event["timestamp_ns"] = str(event["timestamp_ns"])
                event["elapsed_s"] = str(event["elapsed_s"])
                event["datetime"] = datetime.fromisoformat(event["datetime"])

                self.broadcaster.publish(event, key=key)

                message = await asyncio.wait_for(outgoing.get(), 1)
                self.assertEqual(expected, json.loads(message["text"]))
                await incoming.put({"type": "websocket.disconnect", "code": 1000})
                await asyncio.wait_for(task, 1)

    async def test_invalid_measurement_is_not_sent_and_subscription_is_cleaned_up(self):
        for key, path in ((None, "/ws"), ("meter-A", "/ws/meter-A")):
            for changes in (
                {"timestamp_ns": "invalid"}, {"datetime": "invalid"},
                {"data": {"unsupported": object()}},
            ):
                with self.subTest(key=key, changes=changes):
                    task, _, outgoing = await self.connect(path)
                    subscriber = next(iter(self.broadcaster._subscribers))
                    event = self.measurement_event()
                    event.update(changes)

                    self.broadcaster.publish(event, key=key)
                    with self.assertRaises(ValidationError):
                        await asyncio.wait_for(task, 1)

                    self.assertTrue(outgoing.empty())
                    self.assertEqual(0, len(self.broadcaster._subscribers))
                    await asyncio.wait_for(subscriber.join(), 1)

    async def test_missing_measurement_field_is_rejected_before_sending(self):
        task, _, outgoing = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))
        event = self.measurement_event()
        del event["timestamp_ns"]

        self.broadcaster.publish(event, key=None)
        with self.assertRaises(ValidationError):
            await asyncio.wait_for(task, 1)

        self.assertTrue(outgoing.empty())
        self.assertEqual(0, len(self.broadcaster._subscribers))
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_unknown_or_missing_event_type_is_not_sent_and_cleans_up(self):
        for event_type in ("status", "sample", None):
            with self.subTest(event_type=event_type):
                task, _, outgoing = await self.connect("/ws/group")
                subscriber = next(iter(self.broadcaster._subscribers))
                event = self.measurement_event()
                if event_type is None:
                    del event["type"]
                else:
                    event["type"] = event_type

                self.broadcaster.publish(event, key="group")
                with self.assertRaisesRegex(ValueError, "Unsupported event type"):
                    await asyncio.wait_for(task, 1)

                self.assertTrue(outgoing.empty())
                self.assertEqual(0, len(self.broadcaster._subscribers))
                await asyncio.wait_for(subscriber.join(), 1)

    async def test_queue_limit_is_selected_per_connection_independently_of_key(self):
        await self.connect("/ws/group?maxsize=1")
        await self.connect("/ws/group?maxsize=4")

        self.assertEqual([1, 4], [queue.maxsize for queue in self.broadcaster._subscribers])
        self.assertEqual(["group", "group"], list(self.broadcaster._subscribers.values()))

    async def test_invalid_queue_limit_is_rejected_before_subscribing(self):
        for path in ("/ws", "/ws/group"):
            for maxsize in ("0", "-1", "1.5", "abc", ""):
                with self.subTest(path=path, maxsize=maxsize):
                    task, _, outgoing, rejected = await self.open_connection(f"{path}?maxsize={maxsize}")
                    self.assertEqual("websocket.close", rejected["type"])
                    self.assertEqual(1008, rejected["code"])
                    await asyncio.wait_for(task, 1)
                    self.assertTrue(outgoing.empty())
                    self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_idle_camera_disconnect_unregisters_subscription(self):
        task, incoming, _ = await self.connect("/ws/camera-A")

        await incoming.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 1)

        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_camera_shutdown_closes_connection_and_completes_queue(self):
        task, _, outgoing = await self.connect("/ws/camera-A?maxsize=1")
        subscriber = next(iter(self.broadcaster._subscribers))

        self.broadcaster.shutdown()

        message = await asyncio.wait_for(outgoing.get(), 1)
        self.assertEqual("websocket.close", message["type"])
        self.assertEqual(1000, message["code"])
        await asyncio.wait_for(task, 1)
        await asyncio.wait_for(subscriber.join(), 1)
        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_camera_send_disconnect_completes_queue_and_unregisters(self):
        task, _, _ = await self.connect("/ws/camera-A")
        subscriber = next(iter(self.broadcaster._subscribers))
        self.send_error = OSError("Connection closed")

        self.broadcaster.publish(self.preview_event(), key="camera-A")
        await asyncio.wait_for(task, 1)

        await asyncio.wait_for(subscriber.join(), 1)
        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_camera_unexpected_send_error_is_preserved_after_cleanup(self):
        task, _, _ = await self.connect("/ws/camera-A")
        subscriber = next(iter(self.broadcaster._subscribers))
        self.send_error = RuntimeError("send failed")

        self.broadcaster.publish(self.preview_event(), key="camera-A")
        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await asyncio.wait_for(task, 1)

        await asyncio.wait_for(subscriber.join(), 1)
        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_camera_disconnect_cancels_blocked_send(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked_send(websocket, data):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        with patch.object(WebSocket, "send_bytes", new=blocked_send):
            task, incoming, _ = await self.connect("/ws/camera-A")
            subscriber = next(iter(self.broadcaster._subscribers))
            self.broadcaster.publish(self.preview_event(), key="camera-A")
            await asyncio.wait_for(entered.wait(), 1)
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 1)

        self.assertTrue(cancelled.is_set())
        await asyncio.wait_for(subscriber.join(), 1)
        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_camera_endpoint_cancellation_unregisters_subscription(self):
        task, _, _ = await self.connect("/ws/camera-A")

        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertEqual(0, len(self.broadcaster._subscribers))

    async def test_preview_id_does_not_define_subscription_key(self):
        _, _, outgoing = await self.connect("/ws/camera-A")
        subscriber = next(iter(self.broadcaster._subscribers))

        self.broadcaster.publish(self.preview_event("camera-B"), key="camera-A")
        metadata, jpeg = self.decode_preview(await asyncio.wait_for(outgoing.get(), 1))
        self.assertEqual("camera-B", metadata["id"])
        self.assertEqual(PREVIEW_JPEG, jpeg)
        self.assertTrue(outgoing.empty())
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_main_app_registers_camera_route_and_uses_shared_broadcaster(self):
        self.app = create_app()
        async with self.app.router.lifespan_context(self.app):
            self.broadcaster = self.app.state.event_broadcaster
            task, incoming, outgoing = await self.connect("/ws/camera-A")

            self.broadcaster.publish(self.preview_event(), key="camera-A")

            metadata, jpeg = self.decode_preview(await asyncio.wait_for(outgoing.get(), 1))
            self.assertEqual("camera-A", metadata["id"])
            self.assertEqual(PREVIEW_JPEG, jpeg)
            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 1)

    async def test_dummy_camera_acquisition_produces_jpeg_on_websocket_and_stops_worker(self):
        self.app = create_app()
        async with self.app.router.lifespan_context(self.app):
            self.broadcaster = self.app.state.event_broadcaster
            devices = self.app.state.device_manager
            manager = self.app.state.measurement_manager
            preview = self.app.state.camera_preview_service
            devices.scan()
            device_id = next(
                key for key, device in devices.get_all().items() if device.get_info().type == "camera"
            )
            device = devices.get(device_id)
            await asyncio.to_thread(devices.connect, device_id)
            settings = device.get_settings().model_dump() | {
                "width": 32, "height": 24, "fps": 30,
                "roi": {"x": 2, "y": 2, "width": 8, "height": 6},
            }
            await asyncio.to_thread(manager.set_settings, device_id, settings)
            task, incoming, outgoing = await self.connect(f"/ws/{device_id}?maxsize=1")

            with patch.object(preview, "offer_frame", wraps=preview.offer_frame) as offered:
                await asyncio.to_thread(manager.start_sampling, device_id)
                metadata, jpeg = self.decode_preview(await asyncio.wait_for(outgoing.get(), 2))
                matching = next(
                    call for call in offered.call_args_list if call.args[1].frame_index == metadata["frame_index"]
                )
                self.assertEqual(str(matching.args[2]), metadata["timestamp_ns"])
                self.assertEqual(device_id, metadata["id"])
                self.assertEqual((32, 24), (metadata["width"], metadata["height"]))
                with Image.open(BytesIO(jpeg)) as decoded:
                    self.assertEqual((32, 24), decoded.size)
                    self.assertEqual("JPEG", decoded.format)
                await asyncio.to_thread(manager.stop_sampling, device_id)

            await incoming.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait_for(task, 1)
        self.assertFalse(preview._thread.is_alive())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe are required")
    async def test_dummy_camera_preview_and_roi_recording_with_numeric_data_through_api(self):
        for pixel_format in ("mono8", "rgb24", "bgr24"):
            with self.subTest(pixel_format=pixel_format):
                await self.check_dummy_camera_recording(pixel_format)

    async def check_dummy_camera_recording(self, pixel_format):
        """実ドライバーとAPIを通し、プレビュー・ROI動画・数値CSVを同時に検証する。"""
        self.app = create_app()
        with tempfile.TemporaryDirectory(prefix="dummy-camera-backend-") as directory:
            async with self.app.router.lifespan_context(self.app):
                self.broadcaster = self.app.state.event_broadcaster
                preview = self.app.state.camera_preview_service
                async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
                    response = await client.post("/api/devices/scan")
                    self.assertEqual(200, response.status_code, response.text)
                    camera_id = next(item["id"] for item in response.json() if item["type"] == "camera")
                    meter_id = next(item["id"] for item in response.json() if item["type"] == "power_meter")
                    camera_path = f"/api/devices/{camera_id}"
                    meter_path = f"/api/devices/{meter_id}"
                    for path in (camera_path, meter_path):
                        response = await client.post(path + "/connect")
                        self.assertEqual(200, response.status_code, response.text)

                    response = await client.get(camera_path + "/settings")
                    self.assertEqual(200, response.status_code, response.text)
                    settings = response.json() | {
                        "width": 32, "height": 24, "fps": 30, "pixel_format": pixel_format,
                        "roi": {"x": 2, "y": 2, "width": 16, "height": 12},
                    }
                    response = await client.put(camera_path + "/settings", json=settings)
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertEqual(settings, response.json())
                    camera_task, camera_incoming, camera_outgoing = await self.connect(f"/ws/{camera_id}?maxsize=1")
                    numeric_task, numeric_incoming, numeric_outgoing = await self.connect("/ws")
                    for path in (camera_path, meter_path):
                        response = await client.post(path + "/sampling/start")
                        self.assertEqual(200, response.status_code, response.text)
                    response = await client.post("/api/recordings/start", json={
                        "directory": directory, "name": pixel_format,
                        "video_options": {camera_id: {"format": "ffv1"}},
                    })
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertTrue(response.json()["is_recording"])
                    for outgoing in (camera_outgoing, numeric_outgoing):
                        while not outgoing.empty():
                            outgoing.get_nowait()

                    previews = []
                    for _ in range(5):
                        metadata, jpeg = self.decode_preview(await asyncio.wait_for(camera_outgoing.get(), 3))
                        self.assertEqual("camera_preview", metadata["type"])
                        self.assertEqual(camera_id, metadata["id"])
                        self.assertEqual((32, 24), (metadata["width"], metadata["height"]))
                        with Image.open(BytesIO(jpeg)) as image:
                            image.load()
                            self.assertEqual("JPEG", image.format)
                            self.assertEqual((32, 24), image.size)
                            self.assertEqual("L" if pixel_format == "mono8" else "RGB", image.mode)
                        previews.append(metadata)
                    # 記録開始前のイベントを除き、記録中に生成された数値を受け取る。
                    while not numeric_outgoing.empty():
                        numeric_outgoing.get_nowait()
                    message = await asyncio.wait_for(numeric_outgoing.get(), 3)
                    numeric = MeasurementEvent.model_validate_json(message["text"])
                    self.assertEqual(meter_id, numeric.id)
                    self.assertIn("power_w", numeric.data)
                    self.assertIsNone(preview.get_error(camera_id))

                    response = await client.post("/api/recordings/stop")
                    self.assertEqual(200, response.status_code, response.text)
                    self.assertFalse(response.json()["is_recording"])
                    self.assertFalse(response.json()["is_stopping"])
                    self.assertIsNone(response.json()["error"])
                    folder = Path(response.json()["directory"])
                    video = folder / f"{camera_id}.mkv"
                    with Path(str(video) + ".frames.csv").open(encoding="utf-8-sig", newline="") as file:
                        rows = list(csv.DictReader(file))
                    self.assertGreater(len(rows), 0)
                    self.assertEqual(list(range(len(rows))), [int(row["video_frame_index"]) for row in rows])
                    indexes = [int(row["frame_index"]) for row in rows]
                    self.assertEqual(list(range(indexes[0], indexes[0] + len(rows))), indexes)
                    timestamps = [int(row["timestamp_ns"]) for row in rows]
                    self.assertTrue(all(first < second for first, second in zip(timestamps, timestamps[1:])))
                    recorded = {int(row["frame_index"]): row["timestamp_ns"] for row in rows}
                    # 開始直前から変換中だった画像を避け、後半のプレビューと保存フレームを照合する。
                    for metadata in previews[-3:]:
                        self.assertEqual(metadata["timestamp_ns"], recorded[metadata["frame_index"]])
                    with (folder / f"{meter_id}.csv").open(encoding="utf-8-sig", newline="") as file:
                        numeric_rows = list(csv.DictReader(file))
                    self.assertIn(str(numeric.timestamp_ns), [row["timestamp_ns"] for row in numeric_rows])
                    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
                    self.assertEqual(settings, metadata["devices"][camera_id][0]["settings"])
                    self.assertEqual({"state": "completed", "error": None}, metadata["video_results"][camera_id])
                    self.assertIsNotNone(metadata["recording_stopped_at"])

                    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    probe = await asyncio.to_thread(subprocess.run, [
                        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                        "-show_entries", "stream=codec_name,width,height,nb_read_frames", "-of", "json", str(video),
                    ], capture_output=True, check=True, timeout=10, creationflags=flags)
                    stream = json.loads(probe.stdout)["streams"][0]
                    self.assertEqual("ffv1", stream["codec_name"])
                    self.assertEqual((16, 12), (stream["width"], stream["height"]))
                    self.assertEqual(len(rows), int(stream["nb_read_frames"]))
                    decoded = await asyncio.to_thread(subprocess.run, [
                        "ffmpeg", "-v", "error", "-i", str(video), "-f", "rawvideo",
                        "-pix_fmt", "gray" if pixel_format == "mono8" else "rgb24", "pipe:1",
                    ], capture_output=True, check=True, timeout=10, creationflags=flags)
                    # ダミーの縦線をフレーム番号から再現し、ROIとRGB/BGRの色順を画素単位で検証する。
                    background = b"\x10" if pixel_format == "mono8" else b"\x10\x10\x10"
                    line = b"\xc0" if pixel_format == "mono8" else b"\xc0\x60\x20"
                    expected = b"".join(
                        b"".join(line if x == index % 32 else background for x in range(2, 18)) * 12
                        for index in indexes
                    )
                    self.assertEqual(expected, decoded.stdout)

                    # 保存停止後も取得・配信は継続し、取得停止後は新しいプレビューが届かない。
                    response = await client.get(camera_path + "/sampling")
                    self.assertTrue(response.json()["is_sampling"])
                    while not camera_outgoing.empty():
                        camera_outgoing.get_nowait()
                    last, _ = self.decode_preview(await asyncio.wait_for(camera_outgoing.get(), 3))
                    self.assertGreater(last["frame_index"], indexes[-1])
                    response = await client.post(camera_path + "/sampling/stop")
                    self.assertEqual(200, response.status_code, response.text)
                    while not camera_outgoing.empty():
                        camera_outgoing.get_nowait()
                    with self.assertRaises(asyncio.TimeoutError):
                        await asyncio.wait_for(camera_outgoing.get(), 0.15)
                    response = await client.post(camera_path + "/sampling/start")
                    self.assertEqual(200, response.status_code, response.text)
                    restarted, _ = self.decode_preview(await asyncio.wait_for(camera_outgoing.get(), 3))
                    self.assertGreater(restarted["frame_index"], last["frame_index"])
                    self.assertGreater(int(restarted["timestamp_ns"]), int(last["timestamp_ns"]))
                    for path in (camera_path, meter_path):
                        response = await client.post(path + "/disconnect")
                        self.assertEqual(200, response.status_code, response.text)
                        self.assertEqual("disconnected", (await client.get(path)).json()["status"])
                    for task, incoming in ((camera_task, camera_incoming), (numeric_task, numeric_incoming)):
                        await incoming.put({"type": "websocket.disconnect", "code": 1000})
                        await asyncio.wait_for(task, 1)
                    self.assertEqual(0, len(self.broadcaster._subscribers))
            self.assertFalse(preview._thread.is_alive())

    async def test_receives_multiple_events_and_completes_queue_tasks(self):
        _, _, outgoing = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))
        events = [self.measurement_event(power=value) for value in (0.001, 0.002)]

        for event in events:
            self.broadcaster.publish(event, key=None)
            message = await asyncio.wait_for(outgoing.get(), 1)
            self.assertEqual("websocket.send", message["type"])
            self.assertEqual(event, json.loads(message["text"]))
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_default_subscription_receives_only_none_key_and_preserves_queue_limit(self):
        _, _, outgoing = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))
        self.assertEqual(100, subscriber.maxsize)
        self.broadcaster.publish({"jpeg": b"image"}, key="camera")
        event = self.measurement_event()
        self.broadcaster.publish(event, key=None)

        message = await asyncio.wait_for(outgoing.get(), 1)

        self.assertEqual(event, json.loads(message["text"]))
        self.assertTrue(outgoing.empty())
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
        event = self.measurement_event()

        self.broadcaster.publish(event, key=None)

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

        event = self.measurement_event()
        self.broadcaster.publish(event, key=None)
        message = await asyncio.wait_for(second_outgoing.get(), 1)
        self.assertEqual(event, json.loads(message["text"]))

    async def test_transport_disconnect_during_send_is_handled(self):
        task, _, _ = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))
        self.send_error = OSError("Connection closed")

        self.broadcaster.publish(self.measurement_event(), key=None)
        await asyncio.wait_for(task, 1)

        self.assertEqual(0, len(self.broadcaster._subscribers))
        await asyncio.wait_for(subscriber.join(), 1)

    async def test_unexpected_send_error_is_preserved_after_cleanup(self):
        task, _, _ = await self.connect()
        subscriber = next(iter(self.broadcaster._subscribers))
        self.send_error = RuntimeError("send failed")

        self.broadcaster.publish(self.measurement_event(), key=None)
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
            self.broadcaster.publish(self.measurement_event(), key=None)
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
            event = self.measurement_event()
            self.broadcaster.publish(event, key=None)
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
            device_ids = [
                device_id for device_id, device in devices.get_all().items()
                if device.get_info().type == "power_meter"
            ]
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
                    self.assertEqual(manager._get_elapsed(event.timestamp_ns, manager._measurement_period), event.elapsed_s)
                    self.assertEqual(manager._get_datetime(event.timestamp_ns, manager._measurement_period), event.datetime)
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
                device_ids = [
                    device_id for device_id, device in devices.get_all().items()
                    if device.get_info().type == "power_meter"
                ]
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
