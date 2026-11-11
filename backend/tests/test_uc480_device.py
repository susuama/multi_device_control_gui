"""uc480の接続・設定取得・解放失敗を、実機なしで確認する。"""

import unittest
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from pylablib.devices.uc480.uc480 import TDeviceInfo, UC480Camera

from backend.app.devices.camera.Thorlabs_uc480 import uc480Camera
from backend.app.models.devices import CameraROI


class UC480DeviceTests(unittest.TestCase):
    def setUp(self):
        self.device = uc480Camera("camera", serial_number="SERIAL001", address="7")
        self.device.logger = Mock()
        self.cam = Mock(spec=UC480Camera)
        self.cam.is_opened.return_value = True
        self.info = TDeviceInfo(1, "DCC1545M", "Thorlabs", "SERIAL001", "USB2", "", "", 0)
        self.cam.get_device_info.return_value = self.info
        self.cam.get_data_dimensions.return_value = (480, 640)
        self.cam.get_frame_period.return_value = 0.1
        self.cam.get_exposure.return_value = 0.01
        self.cam.get_gains.return_value = (1, 1, 1, 1)
        self.cam.get_gain_boost.return_value = False
        self.cam.get_color_mode.return_value = "mono8"
        self.cam.acquisition_in_progress.return_value = False
        self.cam.TimeoutError = UC480Camera.TimeoutError
        self.find = self.enterContext(patch(
            "backend.app.devices.camera.Thorlabs_uc480.find_by_serial", return_value=7
        ))
        self.open = self.enterContext(patch(
            "backend.app.devices.camera.Thorlabs_uc480.UC480Camera", return_value=self.cam
        ))

    def test_connect_uses_actual_sdk_info_and_reads_settings(self):
        self.assertTrue(self.device.connect())
        self.find.assert_called_once_with("SERIAL001", backend="uc480")
        self.open.assert_called_once_with(dev_id=7, backend="uc480")
        self.assertTrue(self.device.connected)
        self.assertEqual(self.device.get_info().model, "DCC1545M")
        self.assertEqual(self.device.settings.width, 640)
        self.assertEqual(self.device.settings.height, 480)
        self.assertEqual(self.device.settings.fps, 10)
        self.assertEqual(self.device.settings.pixel_format, "mono8")
        self.cam.close.assert_not_called()

    def test_wrong_serial_is_rejected_and_closed(self):
        self.cam.get_device_info.return_value = self.info._replace(serial_number="OTHER")
        with self.assertRaisesRegex(ValueError, "OTHER"):
            self.device.connect()
        self.cam.close.assert_called_once()
        self.assertIsNone(self.device.uc480)
        self.assertFalse(self.device.connected)
        self.assertIsNone(self.device._device_info)

    def test_mono16_is_changed_to_mono8_before_readback(self):
        self.cam.get_color_mode.return_value = "mono16"

        def set_color_mode(mode):
            self.cam.get_color_mode.return_value = mode

        self.cam.set_color_mode.side_effect = set_color_mode
        self.assertTrue(self.device.connect())
        self.cam.set_color_mode.assert_called_once_with("mono8")
        self.assertEqual(self.device.settings.pixel_format, "mono8")

    def test_supported_color_format_is_preserved(self):
        self.cam.get_color_mode.return_value = "rgb8p"
        self.assertTrue(self.device.connect())
        self.assertEqual(self.device.settings.pixel_format, "rgb24")
        self.cam.set_color_mode.assert_not_called()

    def test_setting_failure_closes_camera_and_allows_reconnect(self):
        failure = OSError("settings read failed")
        self.cam.get_exposure.side_effect = failure
        with self.assertRaises(OSError) as raised:
            self.device.connect()
        self.assertIs(raised.exception, failure)
        self.cam.close.assert_called_once()
        self.assertIsNone(self.device.uc480)
        self.assertIsNone(self.device.last_error)
        self.assertFalse(self.device.connected)
        self.cam.get_exposure.side_effect = None
        self.assertTrue(self.device.connect())

    def test_cleanup_failure_keeps_handle_and_original_error_until_disconnect(self):
        failure = OSError("settings read failed")
        self.cam.get_exposure.side_effect = failure
        self.cam.close.side_effect = OSError("close failed")
        with self.assertRaises(OSError) as raised:
            self.device.connect()
        self.assertIs(raised.exception, failure)
        self.assertIs(self.device.uc480, self.cam)
        self.assertIn("close failed", self.device.last_error)
        self.device.logger.exception.assert_called_once()
        with self.assertRaises(RuntimeError):
            self.device.connect()
        self.open.assert_called_once()
        self.cam.close.side_effect = None
        self.assertTrue(self.device.disconnect())
        self.assertIsNone(self.device.uc480)
        self.assertIsNone(self.device.last_error)
        self.assertEqual(self.cam.close.call_count, 2)

    def test_disconnect_failure_preserves_handle_for_retry(self):
        self.device.connect()
        self.cam.close.side_effect = OSError("close failed")
        self.assertFalse(self.device.disconnect())
        self.assertIs(self.device.uc480, self.cam)
        self.assertEqual(self.device.get_info().status, "error")
        self.cam.close.side_effect = None
        self.assertTrue(self.device.disconnect())
        self.assertIsNone(self.device.uc480)
        self.assertEqual(self.device.get_info().status, "disconnected")
        self.assertEqual(self.cam.close.call_count, 2)

    def test_missing_serial_does_not_open_a_camera(self):
        self.device.serial_number = ""
        with self.assertRaises(ValueError):
            self.device.connect()
        self.find.assert_not_called()
        self.open.assert_not_called()

    def test_apply_settings_reads_actual_values_and_preserves_software_roi(self):
        self.device.connect()
        self.cam.acquisition_in_progress.return_value = True
        requested = self.device.settings.model_copy(update={
            "fps": 20, "exposure": 0.02, "gain": 2, "gain_boost": True,
            "roi": CameraROI(x=10, y=20, width=100, height=80),
        })
        applied = self.device.set_settings(requested)
        self.cam.clear_acquisition.assert_called_once()
        self.cam.set_frame_period.assert_called_once_with(0.05)
        self.cam.set_exposure.assert_called_once_with(0.02)
        self.cam.set_gains.assert_called_once_with(master=2)
        self.cam.set_gain_boost.assert_called_once_with(True)
        self.cam.start_acquisition.assert_called_once()
        self.assertEqual(applied.fps, 10)
        self.assertEqual(applied.exposure, 0.01)
        self.assertEqual(applied.roi, requested.roi)
        self.cam.set_roi.assert_not_called()

    def test_setting_failure_stops_capture_without_restarting_or_updating_cache(self):
        self.device.connect()
        original = self.device.get_settings()
        self.cam.acquisition_in_progress.return_value = True
        self.cam.set_exposure.side_effect = OSError("setting failed")
        with self.assertRaises(OSError):
            self.device.set_settings(original)
        self.assertEqual(self.device.get_settings(), original)
        self.assertTrue(self.device._stop_event.is_set())
        self.assertIsNotNone(self.device.last_error)
        self.cam.start_acquisition.assert_not_called()

    def test_size_change_is_rejected_before_writing(self):
        self.device.connect()
        with self.assertRaises(ValueError):
            self.device.set_settings(self.device.settings.model_copy(update={"width": 320}))
        self.cam.clear_acquisition.assert_not_called()

    def frame(self, image=None):
        if image is None:
            image = np.zeros((480, 640), dtype=np.uint8)
        return image, SimpleNamespace(framestamp=123, timestamp_dev=456)

    def test_read_frame_owns_pixels_and_converts_device_time(self):
        self.device.connect()
        image, info = self.frame()
        self.cam.read_oldest_image.return_value = image, info
        frame = self.device._read_frame()
        self.cam.start_acquisition.assert_called_once()
        self.cam.wait_for_frame.assert_called_once_with(timeout=0.1, error_on_stopped=True)
        self.cam.read_oldest_image.assert_called_once_with(return_info=True)
        image[:] = 255
        self.assertEqual(frame.frame_index, 123)
        self.assertEqual(frame.device_timestamp_ns, 45600)
        self.assertTrue(frame.image.readonly)
        self.assertTrue(frame.image.c_contiguous)
        self.assertEqual(frame.image[0, 0], 0)

    def test_read_rgb_preserves_channel_order(self):
        self.cam.get_color_mode.return_value = "rgb8p"
        self.device.connect()
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        image[0, 0] = [10, 20, 30]
        self.cam.read_oldest_image.return_value = self.frame(image)
        frame = self.device._read_frame()
        self.assertEqual(frame.image.shape, (480, 640, 3))
        self.assertEqual([frame.image[0, 0, i] for i in range(3)], [10, 20, 30])

    def test_read_rejects_non_8bit_images_without_truncating_values(self):
        self.device.connect()
        self.cam.read_oldest_image.return_value = self.frame(np.zeros((480, 640), dtype=np.uint16))
        with self.assertRaises(ValueError):
            self.device._read_frame()

    def test_short_wait_timeout_retries_but_total_timeout_propagates(self):
        self.device.connect()
        self.cam.wait_for_frame.side_effect = [self.cam.TimeoutError("wait"), None]
        self.cam.read_oldest_image.return_value = self.frame()
        self.device._read_frame()
        self.assertEqual(self.cam.wait_for_frame.call_count, 2)
        self.cam.wait_for_frame.side_effect = self.cam.TimeoutError("wait")
        with patch("backend.app.devices.camera.Thorlabs_uc480.time.monotonic", side_effect=[0, 3]):
            with self.assertRaises(self.cam.TimeoutError):
                self.device._read_frame()

    def test_stop_request_interrupts_frame_wait(self):
        self.device.connect()

        def wait(**kwargs):
            self.device._stop_event.set()
            raise self.cam.TimeoutError("wait")

        self.cam.wait_for_frame.side_effect = wait
        with self.assertRaises(InterruptedError):
            self.device._read_frame()
        self.cam.read_oldest_image.assert_not_called()

    def test_sampling_stops_sdk_after_delivering_frame(self):
        self.device.connect()
        self.cam.read_oldest_image.return_value = self.frame()

        def put(data):
            self.assertEqual(data.type, "frame")
            self.device._stop_event.set()

        with patch.object(self.device, "_put_data", side_effect=put):
            self.device._sample_data()
        self.cam.stop_acquisition.assert_called_once()
        self.assertIsNone(self.device.last_error)

    def test_sampling_thread_can_stop_while_waiting_for_frame(self):
        self.device.connect()
        entered = threading.Event()

        def wait(**kwargs):
            entered.set()
            self.device._stop_event.wait(0.01)
            raise self.cam.TimeoutError("wait")

        self.cam.wait_for_frame.side_effect = wait
        queue = Queue()
        thread = self.device.start_sampling(queue)
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(queue.get(timeout=1).type, "settings")
        finally:
            self.device.stop_sampling()
        self.assertFalse(thread.is_alive())
        self.assertIsNone(self.device.last_error)
        self.assertTrue(self.device.connected)
        self.assertIsNone(self.device.data_queue)
        self.assertGreaterEqual(self.cam.stop_acquisition.call_count, 1)


if __name__ == "__main__":
    unittest.main()
