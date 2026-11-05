"""ダミーカメラのフレーム契約、設定通知、停止と既存APIへの登録を検証する。"""

import logging
import queue
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.devices.camera.dummy_camera import DummyCamera
from backend.app.devices.registry import DEVICE_CLASSES
from backend.app.main import create_app
from backend.app.services.device_manager import DeviceManager


class DummyCameraTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.camera = DummyCamera("camera", "test", "dummy")
        self.addCleanup(self.camera.disconnect)
        settings = self.camera.get_settings().model_dump()
        self.camera.set_settings(settings | {"width": 8, "height": 6, "fps": 100})

    def test_registry_scan_keeps_camera_identity(self):
        manager = DeviceManager(DEVICE_CLASSES)
        self.addCleanup(manager.shutdown)
        manager.scan()
        cameras = {key: device for key, device in manager.get_all().items() if isinstance(device, DummyCamera)}
        self.assertEqual(1, len(cameras))
        device_id, device = next(iter(cameras.items()))
        self.assertEqual("camera", device.get_info().type)
        manager.scan()
        self.assertIs(device, manager.get(device_id))

    def test_disconnected_camera_rejects_acquisition(self):
        with self.assertRaises(RuntimeError):
            self.camera.read()
        with self.assertRaises(RuntimeError):
            self.camera.start_sampling(queue.Queue())
        self.assertTrue(self.camera.disconnect())
        self.camera.update_address("new_address")
        self.camera.connect()
        with self.assertRaises(RuntimeError):
            self.camera.update_address("another_address")

    def test_frames_match_formats_and_keep_previous_pixels(self):
        self.camera.connect()
        for pixel_format, channels in (("mono8", 1), ("rgb24", 3), ("bgr24", 3)):
            with self.subTest(pixel_format=pixel_format):
                settings = self.camera.get_settings().model_dump()
                self.camera.set_settings(settings | {
                    "pixel_format": pixel_format, "roi": {"x": 1, "y": 1, "width": 4, "height": 3},
                })
                first = self.camera.read()
                pixels = first.data.image.tobytes()
                second = self.camera.read()
                shape = (6, 8) if channels == 1 else (6, 8, 3)
                self.assertEqual("frame", first.type)
                self.assertEqual(shape, first.data.image.shape)
                self.assertEqual("B", first.data.image.format)
                self.assertTrue(first.data.image.c_contiguous)
                self.assertIsNone(first.data.device_timestamp_ns)
                self.assertGreaterEqual(second.timestamp_ns, first.timestamp_ns)
                self.assertEqual(first.data.frame_index + 1, second.data.frame_index)
                self.assertEqual(pixels, first.data.image.tobytes())
                self.assertNotEqual(pixels, second.data.image.tobytes())
                offset = (first.data.frame_index % 8) * channels
                expected = {"mono8": b"\xc0", "rgb24": b"\xc0\x60\x20", "bgr24": b"\x20\x60\xc0"}
                self.assertEqual(expected[pixel_format], pixels[offset:offset + channels])

    def test_settings_are_validated_and_copied(self):
        requested = self.camera.get_settings()
        requested.roi = None
        requested.gain = 2
        applied = self.camera.set_settings(requested)
        requested.gain = 3
        applied.gain = 4
        self.assertEqual(2, self.camera.get_settings().gain)
        invalid = self.camera.get_settings().model_dump() | {"fps": 0}
        with self.assertRaises(ValidationError):
            self.camera.set_settings(invalid)
        self.assertEqual(100, self.camera.get_settings().fps)

    def test_start_stop_and_restart_preserve_notification_and_frame_order(self):
        self.camera.connect()
        output = queue.Queue()
        thread = self.camera.start_sampling(output)
        self.assertIs(thread, self.camera.start_sampling(output))
        self.assertEqual("settings", output.get(timeout=1).type)
        first = output.get(timeout=1)
        self.assertEqual("frame", first.type)
        self.camera.stop_sampling()
        self.assertFalse(thread.is_alive())
        self.assertIsNone(self.camera.data_queue)
        restarted = queue.Queue()
        self.camera.start_sampling(restarted)
        self.assertEqual("settings", restarted.get(timeout=1).type)
        self.assertGreater(restarted.get(timeout=1).data.frame_index, first.data.frame_index)
        self.assertTrue(self.camera.disconnect())
        self.assertFalse(self.camera.is_sampling())

    def test_settings_notification_precedes_frames_using_new_shape(self):
        self.camera.connect()
        output = queue.Queue()
        self.camera.start_sampling(output)
        self.assertEqual("settings", output.get(timeout=1).type)
        self.assertEqual((6, 8), output.get(timeout=1).data.image.shape)
        self.camera.set_settings(self.camera.get_settings().model_dump() | {"width": 10})
        while True:
            message = output.get(timeout=1)
            if message.type == "settings":
                self.assertEqual(10, message.data["width"])
                break
            self.assertEqual((6, 8), message.data.image.shape)
        self.assertEqual((6, 10), output.get(timeout=1).data.image.shape)

    def test_failed_apply_keeps_previous_settings(self):
        original = self.camera.get_settings()
        with patch.object(self.camera, "_apply_settings", side_effect=OSError("apply failed")):
            with self.assertRaisesRegex(OSError, "apply failed"):
                self.camera.set_settings(original.model_dump() | {"gain": 2})
        self.assertEqual(original, self.camera.get_settings())

    def test_acquisition_failure_is_reported_and_disconnect_allows_reconnect(self):
        self.camera.connect()
        with patch.object(self.camera, "_read_frame", side_effect=OSError("read failed")):
            thread = self.camera.start_sampling(queue.Queue())
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual("error", self.camera.get_info().status)
        self.assertEqual("read failed", self.camera.last_error)
        with self.assertRaises(RuntimeError):
            self.camera.connect()
        self.assertTrue(self.camera.disconnect())
        self.assertTrue(self.camera.connect())
        self.assertEqual("frame", self.camera.read().type)

    def test_stop_timeout_keeps_thread_and_does_not_close_device(self):
        self.camera.connect()
        entered, release = threading.Event(), threading.Event()
        read_frame = self.camera._read_frame

        def blocked_frame():
            entered.set()
            release.wait(2)
            return read_frame()

        try:
            with patch.object(self.camera, "_read_frame", side_effect=blocked_frame), \
                    patch.object(self.camera, "_close_device") as close_device, \
                    patch.object(self.camera, "STOP_TIMEOUT", 0.01):
                thread = self.camera.start_sampling(queue.Queue())
                self.assertTrue(entered.wait(1))
                with self.assertRaises(TimeoutError):
                    self.camera.stop_sampling()
                self.assertIs(thread, self.camera._sampling_thread)
                self.assertFalse(self.camera.disconnect())
                close_device.assert_not_called()
                with self.assertRaises(RuntimeError):
                    self.camera.start_sampling(queue.Queue())
        finally:
            release.set()
            self.camera.stop_sampling()

    def test_existing_api_controls_registered_camera(self):
        app = create_app()
        with TestClient(app) as client:
            self.assertEqual(200, client.post("/api/devices/scan").status_code)
            cameras = [item for item in client.get("/api/devices").json() if item["type"] == "camera"]
            self.assertEqual(1, len(cameras))
            device_id = cameras[0]["id"]
            path = f"/api/devices/{device_id}"
            self.assertEqual(200, client.post(path + "/connect").status_code)
            settings = client.get(path + "/settings").json() | {"width": 8, "height": 6}
            response = client.put(path + "/settings", json=settings)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(settings, response.json())
            self.assertEqual(200, client.post(path + "/sampling/start").status_code)
            self.assertEqual(200, client.post(path + "/sampling/stop").status_code)
            app.state.measurement_manager.data_queue.join()
            self.assertEqual(settings, app.state.measurement_manager.latest_settings[device_id].data)
            self.assertEqual(200, client.post(path + "/disconnect").status_code)
            self.assertEqual("disconnected", client.get(path).json()["status"])
