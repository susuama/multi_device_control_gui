"""接続監視、取得失敗の保存、エラー後のリソース解放を確認する。"""

import json
import logging
import shutil
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from backend.app.api.devices import disconnect_device, get_devices
from backend.app.devices.camera.Thorlabs_uc480 import UC480, uc480Error
from backend.app.devices.power_meter.Thorlabs_PM100A import PM100A
from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.services.device_manager import DeviceManager
from backend.app.services.event_broadcaster import EventBroadcaster
from backend.app.services.measurement_manager import MeasurementManager


class DeviceConnectionTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch(
            "backend.app.devices.camera.Thorlabs_uc480.list_cameras",
            return_value=[SimpleNamespace(serial_number="serial")],
        ))

    def devices(self):
        camera = UC480("camera", serial_number="serial")
        camera.uc480 = Mock()
        camera.uc480.get_device_info.return_value = SimpleNamespace(serial_number="serial")
        meter = PM100A("meter")
        meter.pm100a = Mock()
        meter.pm100a.get_id.return_value = "Thorlabs,PM100A,serial,1"
        for device in (camera, meter):
            device.connected = True
            device.logger = Mock()
        return camera, meter

    def test_idle_list_checks_sdk_and_preserves_error_until_disconnect(self):
        camera, meter = self.devices()
        manager = DeviceManager([])
        manager.register(camera.device_id, camera)
        manager.register(meter.device_id, meter)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(device_manager=manager)))
        self.assertTrue(all(info.status == "connected" for info in get_devices(request)))
        meter.pm100a.get_id.assert_called_once_with(timeout=1.0)
        camera.uc480.get_device_info.side_effect = OSError("camera unplugged")
        meter.pm100a.get_id.side_effect = OSError("meter unplugged")
        infos = get_devices(request)
        self.assertEqual([info.status for info in infos], ["error", "error"])
        self.assertEqual([info.error for info in infos], ["camera unplugged", "meter unplugged"])
        for device in (camera, meter):
            device.is_connected = Mock(return_value=True)
            self.assertEqual(device.get_info().status, "error")
            device.is_connected.assert_not_called()
            with self.assertRaises(RuntimeError):
                device.connect()
            self.assertTrue(device.disconnect())
            self.assertIsNone(device.last_error)
            with patch.object(device, "_open_device", return_value=device.settings):
                self.assertTrue(device.connect())
                self.assertTrue(device.connected)

    def test_false_probe_sets_error_without_overwriting_acquisition_reason(self):
        for device in self.devices():
            with self.subTest(device=device.device_id):
                device.is_connected = Mock(return_value=False)
                device.last_error = "original read error"
                self.assertEqual(device.get_info().error, "original read error")
                self.assertFalse(device.connected)

    def test_camera_closes_even_when_acquisition_stop_fails(self):
        camera, _ = self.devices()
        cam = camera.uc480
        cam.stop_acquisition.side_effect = OSError("already unplugged")
        self.assertTrue(camera.disconnect())
        cam.close.assert_called_once()
        self.assertIsNone(camera.uc480)

    def test_camera_missing_from_sdk_list_is_detected_with_open_handle(self):
        camera, _ = self.devices()
        with patch("backend.app.devices.camera.Thorlabs_uc480.list_cameras", return_value=[]):
            self.assertEqual(camera.get_info().status, "error")
        self.assertFalse(camera.connected)
        self.assertIsNotNone(camera.uc480)

    def test_sdk_cleanup_failure_still_attempts_native_handle_release(self):
        camera, _ = self.devices()
        cam = camera.uc480
        cam.close.side_effect = uc480Error("clear acquisition failed")
        cam.hcam = 7
        self.assertTrue(camera.disconnect())
        cam.lib.is_ExitCamera.assert_called_once_with(7)
        self.assertIsNone(camera.uc480)

    def test_close_failure_keeps_handle_and_reason_for_retry(self):
        for device, attribute in ((self.devices()[0], "uc480"), (self.devices()[1], "pm100a")):
            with self.subTest(device=device.device_id):
                handle = getattr(device, attribute)
                handle.close.side_effect = OSError("close failed")
                device.last_error = "read failed"
                self.assertFalse(device.disconnect())
                self.assertIs(getattr(device, attribute), handle)
                self.assertIn("read failed", device.last_error)
                self.assertIn("close failed", device.last_error)
                handle.close.side_effect = None
                self.assertTrue(device.disconnect())
                self.assertIsNone(getattr(device, attribute))

    def test_native_release_failure_keeps_camera_handle_for_retry(self):
        camera, _ = self.devices()
        cam = camera.uc480
        cam.close.side_effect = uc480Error("clear acquisition failed")
        cam.lib.is_ExitCamera.side_effect = uc480Error("release failed")
        self.assertFalse(camera.disconnect())
        self.assertIs(camera.uc480, cam)
        self.assertIn("release failed", camera.last_error)
        cam.close.side_effect = None
        self.assertTrue(camera.disconnect())

    def test_live_sampling_thread_prevents_handle_close(self):
        for device in self.devices():
            with self.subTest(device=device.device_id):
                device.STOP_TIMEOUT = 0.01
                gate = threading.Event()
                worker = threading.Thread(target=gate.wait)
                worker.start()
                device._sampling_thread = worker
                handle = device.uc480 if isinstance(device, UC480) else device.pm100a
                try:
                    self.assertFalse(device.disconnect())
                    handle.close.assert_not_called()
                    self.assertIs(device._sampling_thread, worker)
                finally:
                    gate.set()
                    worker.join(1)
                    device.disconnect()

    def test_disconnect_api_does_not_repeat_failing_camera_stop(self):
        camera, _ = self.devices()
        camera.uc480.stop_acquisition.side_effect = OSError("unplugged")
        manager = DeviceManager([])
        manager.register("camera", camera)
        measurement = Mock()
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            device_manager=manager, measurement_manager=measurement,
        )))
        self.assertEqual(disconnect_device("camera", request), {"success": True})
        measurement.stop_sampling.assert_not_called()
        measurement.camera_preview_service.stop_camera.assert_called_once_with("camera")


class AcquisitionErrorRecordingTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.dm = DeviceManager([DummyPowerMeter])
        self.mm = MeasurementManager(self.dm, EventBroadcaster())
        self.dm.scan()
        self.ids = list(self.dm.get_all())
        for device_id in self.ids:
            self.dm.connect(device_id)
            self.mm.start_sampling(device_id)
        self.parent = Path("test-results").resolve()
        self.directory = self.parent / ("error-recovery-" + uuid4().hex)
        self.directory.mkdir(parents=True)
        self.addCleanup(self.remove_directory)
        self.addCleanup(self.dm.shutdown)
        self.addCleanup(self.mm.shutdown)

    def remove_directory(self):
        # このテストが作成したフォルダだけを削除する。
        assert self.directory.resolve().parent == self.parent
        shutil.rmtree(self.directory)

    def test_error_is_saved_without_stopping_other_devices_or_recording(self):
        self.mm.start_recording(str(self.directory), name="first")
        failed = self.dm.get(self.ids[0])
        with patch.object(failed, "_read_sample", side_effect=OSError("injected USB failure")):
            failed._sampling_thread.join(2)
        deadline = time.monotonic() + 2
        while not self.mm.get_recording_status().targets[self.ids[0]].acquisition_error:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        status = self.mm.get_recording_status()
        self.assertTrue(status.is_recording)
        self.assertIsNone(status.error)
        self.assertTrue(self.dm.get(self.ids[1]).is_sampling())
        self.assertTrue(self.mm._is_processor_running())
        self.assertEqual(status.targets[self.ids[0]].acquisition_error, "injected USB failure")
        self.assertEqual(status.targets[self.ids[0]].state, "recording")
        self.assertIsNone(status.targets[self.ids[0]].error)
        self.dm.disconnect(self.ids[0])
        self.assertEqual(self.mm.get_recording_status().targets[self.ids[0]].acquisition_error,
                         "injected USB failure")
        self.dm.connect(self.ids[0])
        self.mm.start_sampling(self.ids[0])
        self.assertTrue(failed.is_sampling())
        self.assertIsNone(failed.last_error)
        self.mm.stop_recording()
        metadata = json.loads((self.directory / "first" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(len(metadata["acquisition_errors"]), 1)
        self.assertEqual(metadata["acquisition_errors"][0]["device_id"], self.ids[0])
        self.assertEqual(metadata["acquisition_errors"][0]["message"], "injected USB failure")
        self.mm.start_recording(str(self.directory), name="second")
        self.assertEqual(self.mm.get_metadata().acquisition_errors, [])
        self.assertTrue(all(target.acquisition_error is None
                            for target in self.mm.get_recording_status().targets.values()))
        self.mm.stop_recording()


if __name__ == "__main__":
    unittest.main()
