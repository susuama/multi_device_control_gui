"""プレビューの画像コピー、JPEG変換、最新画像の保持と停止を確認する。"""

import logging
import queue
import threading
import time
import unittest
from array import array
from io import BytesIO
from unittest.mock import Mock, patch

from PIL import Image

from backend.app.models.devices import CameraSetting, DeviceInfo
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.services.camera_preview import CameraPreviewService
from backend.app.services.measurement_manager import MeasurementManager
from backend.app.utils.time_utils import get_counter_ns


class CameraPreviewTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        logging.disable(logging.CRITICAL)
        self.output = queue.Queue()
        self.broadcaster = Mock()
        self.broadcaster.publish.side_effect = self.collect
        self.service = CameraPreviewService(self.broadcaster)
        self.addCleanup(lambda: self.service.shutdown())
        self.service.start_camera("camera")
        self.settings = CameraSetting(
            width=16, height=12, fps=100, exposure=0.01, gain=1,
            roi={"x": 3, "y": 2, "width": 4, "height": 4},
        )

    def collect(self, event, *, key):
        self.output.put((event, key, time.monotonic()))

    def frame(self, index=0, pixel=b"\x40", pixel_format="mono8"):
        shape = (self.settings.height, self.settings.width)
        if pixel_format != "mono8":
            shape += (3,)
        return CameraFrame(memoryview(pixel * (shape[0] * shape[1])).cast("B", shape=shape), index)

    def offer(self, index=0, device_id="camera"):
        return self.service.offer_frame(device_id, self.frame(index), get_counter_ns(), self.settings)

    def wait_until(self, condition):
        deadline = time.monotonic() + 2
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(condition())

    def block_encoder(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        encode = self.service._encode_jpeg

        def delayed(frame):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release JPEG conversion.")
            return encode(frame)

        self.enterContext(patch.object(self.service, "_encode_jpeg", side_effect=delayed))
        return entered, release

    def test_three_pixel_formats_keep_full_size_color_and_frame_metadata(self):
        for pixel_format, pixel, expected in (
            ("mono8", b"\x46", 70),
            ("rgb24", bytes((210, 110, 30)), (210, 110, 30)),
            ("bgr24", bytes((30, 110, 210)), (210, 110, 30)),
        ):
            with self.subTest(pixel_format=pixel_format):
                settings = self.settings.model_copy(update={"pixel_format": pixel_format})
                timestamp = get_counter_ns()
                frame = self.frame(7, pixel, pixel_format)
                self.assertTrue(self.service.offer_frame("camera", frame, timestamp, settings))
                event, key, _ = self.output.get(timeout=2)
                self.assertEqual("camera", key)
                self.assertEqual("camera_preview", event["type"])
                self.assertEqual(7, event["frame_index"])
                self.assertEqual(timestamp, event["timestamp_ns"])
                self.assertEqual((16, 12), (event["width"], event["height"]))
                with Image.open(BytesIO(event["jpeg"])) as decoded:
                    self.assertEqual("JPEG", decoded.format)
                    self.assertEqual((16, 12), decoded.size)
                    actual = decoded.getpixel((8, 6))
                if isinstance(expected, int):
                    self.assertLessEqual(abs(expected - actual), 3)
                else:
                    for wanted, value in zip(expected, actual):
                        self.assertLessEqual(abs(wanted - value), 3)

    def test_frame_pixels_and_settings_are_copied_before_returning(self):
        entered, release = self.block_encoder()
        pixels = bytearray([30] * (16 * 12))
        frame = CameraFrame(memoryview(pixels).cast("B", shape=(12, 16)), 7)
        timestamp = get_counter_ns()
        self.assertTrue(self.service.offer_frame("camera", frame, timestamp, self.settings))
        self.assertTrue(entered.wait(1))

        pixels[:] = bytes([255] * len(pixels))
        frame.frame_index = 99
        self.settings.width = 100
        self.settings.pixel_format = "bgr24"
        release.set()

        event, _, _ = self.output.get(timeout=2)
        self.assertEqual(7, event["frame_index"])
        self.assertEqual(timestamp, event["timestamp_ns"])
        self.assertEqual((16, 12), (event["width"], event["height"]))
        with Image.open(BytesIO(event["jpeg"])) as decoded:
            self.assertLessEqual(abs(decoded.getpixel((8, 6)) - 30), 3)

    def test_slow_conversion_keeps_only_latest_waiting_frame(self):
        entered, release = self.block_encoder()
        self.offer(0)
        self.assertTrue(entered.wait(1))
        for index in range(1, 8):
            self.offer(index)
        release.set()

        self.assertEqual([0, 7], [self.output.get(timeout=2)[0]["frame_index"] for _ in range(2)])
        self.assertTrue(self.output.empty())

    def test_pending_frames_are_kept_independently_for_each_camera(self):
        self.service.start_camera("other")
        entered, release = self.block_encoder()
        self.offer(0)
        self.assertTrue(entered.wait(1))
        for index in (1, 2):
            self.offer(index)
        for index in (9, 10):
            self.offer(index, "other")
        release.set()

        self.assertEqual(0, self.output.get(timeout=2)[0]["frame_index"])
        remaining = [self.output.get(timeout=2) for _ in range(2)]
        self.assertEqual({"camera": 2, "other": 10}, {key: event["frame_index"] for event, key, _ in remaining})

    def test_publish_frequency_does_not_exceed_configured_limit(self):
        self.service.shutdown()
        self.service = CameraPreviewService(self.broadcaster, max_fps=20)
        self.service.start_camera("camera")
        times = []
        for index in range(4):
            self.offer(index)
            times.append(self.output.get(timeout=2)[2])
        for previous, current in zip(times, times[1:]):
            self.assertGreaterEqual(current - previous, 0.045)

    def test_stop_discards_in_progress_result_and_rejects_further_frames(self):
        entered, release = self.block_encoder()
        self.offer(0)
        self.assertTrue(entered.wait(1))
        self.service.stop_camera("camera")
        self.service.stop_camera("camera")
        self.assertFalse(self.offer(1))
        self.service.start_camera("other")
        self.offer(2, "other")
        release.set()

        event, key, _ = self.output.get(timeout=2)
        self.assertEqual(("other", 2), (key, event["frame_index"]))
        self.assertTrue(self.output.empty())

    def test_restart_rejects_previous_acquisition_frames_and_conversion_result(self):
        entered, release = self.block_encoder()
        self.offer(0)
        self.assertTrue(entered.wait(1))
        old_timestamp = get_counter_ns()
        self.service.stop_camera("camera")
        self.service.start_camera("camera")
        self.assertFalse(self.service.offer_frame("camera", self.frame(1), old_timestamp, self.settings))
        self.assertTrue(self.offer(2))
        release.set()

        self.assertEqual(2, self.output.get(timeout=2)[0]["frame_index"])
        self.assertTrue(self.output.empty())

    def test_invalid_shape_dtype_and_noncontiguous_image_are_rejected(self):
        for image in (memoryview(b"\x00" * 10), memoryview(array("H", [0] * (16 * 12)))):
            with self.subTest(image=image), self.assertRaisesRegex(ValueError, "uint8"):
                self.service.offer_frame("camera", CameraFrame(image, 0), get_counter_ns(), self.settings)
        image = memoryview(bytes(range(16))).cast("B", shape=(4, 4))[::2]
        settings = CameraSetting(width=4, height=2, fps=10, exposure=0.01, gain=1)
        with self.assertRaisesRegex(ValueError, "C-contiguous"):
            self.service.offer_frame("camera", CameraFrame(image, 0), get_counter_ns(), settings)
        self.assertTrue(self.output.empty())

    def test_conversion_failure_is_reported_and_next_valid_frame_recovers(self):
        with patch.object(self.service, "_encode_jpeg", side_effect=OSError("encoder failed")):
            self.offer(0)
            self.wait_until(lambda: self.service.get_error("camera") is not None)
            self.assertIn("encoder failed", self.service.get_error("camera"))
            self.assertTrue(self.service._thread.is_alive())
            self.assertTrue(self.output.empty())

        self.offer(1)
        self.assertEqual(1, self.output.get(timeout=2)[0]["frame_index"])
        self.wait_until(lambda: self.service.get_error("camera") is None)

    def test_publish_failure_does_not_terminate_worker(self):
        self.broadcaster.publish.side_effect = OSError("publish failed")
        self.offer(0)
        self.wait_until(lambda: self.service.get_error("camera") is not None)
        self.assertIn("publish failed", self.service.get_error("camera"))
        self.broadcaster.publish.side_effect = self.collect
        self.offer(1)
        self.assertEqual(1, self.output.get(timeout=2)[0]["frame_index"])

    def test_shutdown_timeout_can_be_retried_and_does_not_publish_old_result(self):
        entered, release = self.block_encoder()
        self.offer(0)
        self.assertTrue(entered.wait(1))
        with patch.object(self.service, "STOP_TIMEOUT", 0.01):
            with self.assertRaises(TimeoutError):
                self.service.shutdown()
        self.assertFalse(self.offer(1))
        with self.assertRaises(RuntimeError):
            self.service.start_camera("camera")
        release.set()

        self.service.shutdown()
        self.service.shutdown()
        self.assertFalse(self.service._thread.is_alive())
        self.assertTrue(self.output.empty())

    def test_invalid_conversion_options_are_rejected(self):
        for max_fps in (0, -1, float("nan"), float("inf")):
            with self.subTest(max_fps=max_fps), self.assertRaises(ValueError):
                CameraPreviewService(self.broadcaster, max_fps=max_fps)
        for quality in (0, 96, 1.5, True):
            with self.subTest(quality=quality), self.assertRaises(ValueError):
                CameraPreviewService(self.broadcaster, jpeg_quality=quality)


class MeasurementPreviewTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(logging.disable, logging.root.manager.disable)
        logging.disable(logging.CRITICAL)
        self.settings = CameraSetting(width=16, height=12, fps=100, exposure=0.01, gain=1)
        self.devices = {}
        for device_id, kind in (("camera", "camera"), ("meter", "power_meter")):
            device = Mock()
            device.is_sampling.return_value = False
            device.get_info.return_value = DeviceInfo(
                id=device_id, type=kind, manufacturer="test", model="synthetic",
                serial_number=device_id, address="test", status="connected",
            )
            settings = self.settings.model_dump() if kind == "camera" else {"wavelength": 550.0}

            def start(data_queue, device=device, device_id=device_id, settings=settings, *, on_queue_full=None):
                device.is_sampling.return_value = True
                data_queue.put(DeviceData(device_id, "settings", get_counter_ns(), settings))

            device.start_sampling.side_effect = start
            device.stop_sampling.side_effect = lambda device=device: setattr(device.is_sampling, "return_value", False)
            self.devices[device_id] = device
        manager = Mock()
        manager.get.side_effect = self.devices.__getitem__
        manager.get_all.return_value = self.devices
        self.output = queue.Queue()
        self.broadcaster = Mock()
        self.broadcaster.is_running.return_value = True
        self.broadcaster.publish.side_effect = lambda event, *, key: self.output.put((event, key))
        self.preview = CameraPreviewService(self.broadcaster)
        self.mm = MeasurementManager(manager, self.broadcaster, self.preview)
        self.releases = []
        self.addCleanup(self.shutdown)
        for device_id in self.devices:
            self.mm.start_sampling(device_id)
        self.mm.data_queue.join()

    def shutdown(self):
        for release in self.releases:
            release.set()
        try:
            self.mm.shutdown()
        finally:
            self.preview.shutdown()

    def frame(self, index=0, timestamp_ns=None):
        image = memoryview(b"\x40" * (16 * 12)).cast("B", shape=(12, 16))
        data = DeviceData("camera", "frame", timestamp_ns or get_counter_ns(), CameraFrame(image, index))
        self.mm.data_queue.put(data)
        return data

    def block_encoder(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        encode = self.preview._encode_jpeg

        def delayed(frame):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release JPEG conversion.")
            return encode(frame)

        self.enterContext(patch.object(self.preview, "_encode_jpeg", side_effect=delayed))
        return entered, release

    def test_slow_preview_keeps_all_recording_frames_and_numeric_events_continue(self):
        entered, release = self.block_encoder()
        with patch.object(self.mm._data_recorder, "is_recording", return_value=True), \
                patch.object(self.mm._data_recorder, "write") as write:
            self.frame(0)
            self.assertTrue(entered.wait(1))
            for index in range(1, 6):
                self.frame(index)
            self.mm.data_queue.put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 0.5}))
            self.mm.data_queue.join()

            saved = [call.args[0].data.frame_index for call in write.call_args_list if call.args[0].type == "frame"]
            self.assertEqual(list(range(6)), saved)
            event, key = self.output.get(timeout=1)
            self.assertEqual(("measurement", "meter", None), (event["type"], event["id"], key))

        release.set()
        self.assertEqual([0, 5], [self.output.get(timeout=2)[0]["frame_index"] for _ in range(2)])

    def test_preview_copy_failure_does_not_skip_recording(self):
        with patch.object(self.preview, "offer_frame", side_effect=ValueError("copy failed")), \
                patch.object(self.mm._data_recorder, "is_recording", return_value=True), \
                patch.object(self.mm._data_recorder, "write") as write:
            data = self.frame()
            self.mm.data_queue.join()

        write.assert_called_once()
        self.assertIs(data, write.call_args.args[0])
        self.assertTrue(self.output.empty())

    def test_recording_failure_does_not_skip_preview(self):
        with patch.object(self.mm._data_recorder, "is_recording", return_value=True), \
                patch.object(self.mm._data_recorder, "write", side_effect=OSError("write failed")), \
                patch.object(self.mm, "_request_recording_stop") as finish:
            data = self.frame(7)
            self.mm.data_queue.join()

        finish.assert_called_once()
        self.assertIsInstance(finish.call_args.kwargs["error"], OSError)
        event, key = self.output.get(timeout=2)
        self.assertEqual(("camera", 7, data.timestamp_ns), (key, event["frame_index"], event["timestamp_ns"]))

    def test_numeric_publish_failure_keeps_recording_and_processor_continues(self):
        samples = [DeviceData("meter", "sample", get_counter_ns(), {"power_w": value}) for value in (0.5, 1.0)]

        def publish(event, *, key):
            if event["data"]["power_w"] == 0.5:
                raise RuntimeError("publish failed")
            self.output.put((event, key))

        with patch.object(self.broadcaster, "publish", side_effect=publish), \
                patch.object(self.mm._data_recorder, "is_recording", return_value=True), \
                patch.object(self.mm._data_recorder, "write") as write, \
                patch.object(self.mm, "_request_recording_stop") as finish, \
                patch.object(self.mm.logger, "exception") as log:
            for sample in samples:
                self.mm.data_queue.put(sample)
            self.mm.data_queue.join()

        self.assertEqual(samples, [call.args[0] for call in write.call_args_list])
        finish.assert_not_called()
        log.assert_called_once_with("Could not publish measurement event for %s", "meter")
        event, key = self.output.get(timeout=1)
        self.assertEqual((1.0, None), (event["data"]["power_w"], key))
        self.assertTrue(self.mm._processor_thread.is_alive())

    def test_numeric_event_validation_failure_does_not_skip_recording(self):
        sample = DeviceData("meter", "sample", get_counter_ns(), {"unsupported": object()})
        with patch.object(self.mm._data_recorder, "is_recording", return_value=True), \
                patch.object(self.mm._data_recorder, "write") as write, \
                patch.object(self.mm.logger, "exception") as log:
            self.mm.data_queue.put(sample)
            self.mm.data_queue.join()

        write.assert_called_once()
        self.assertIs(sample, write.call_args.args[0])
        log.assert_called_once_with("Could not publish measurement event for %s", "meter")
        self.assertTrue(self.output.empty())

    def test_frame_without_preview_service_is_recorded_without_numeric_conversion(self):
        with patch.object(self.mm, "camera_preview_service", None), \
                patch("backend.app.services.measurement_manager.MeasurementEvent") as measurement, \
                patch.object(self.mm._data_recorder, "is_recording", return_value=True), \
                patch.object(self.mm._data_recorder, "write") as write:
            data = self.frame()
            self.mm.data_queue.join()

        measurement.assert_not_called()
        write.assert_called_once()
        self.assertIs(data, write.call_args.args[0])
        self.assertTrue(self.output.empty())

    def test_recording_failure_does_not_skip_numeric_publish(self):
        sample = DeviceData("meter", "sample", get_counter_ns(), {"power_w": 0.5})
        with patch.object(self.mm._data_recorder, "is_recording", return_value=True), \
                patch.object(self.mm._data_recorder, "write", side_effect=OSError("write failed")), \
                patch.object(self.mm, "_request_recording_stop") as finish:
            self.mm.data_queue.put(sample)
            self.mm.data_queue.join()

        finish.assert_called_once()
        event, key = self.output.get(timeout=1)
        self.assertEqual(("meter", 0.5, None), (event["id"], event["data"]["power_w"], key))

    def test_stop_and_restart_drop_in_progress_and_old_queued_frames(self):
        entered, release = self.block_encoder()
        self.frame(0)
        self.assertTrue(entered.wait(1))
        previous_timestamp = get_counter_ns()
        self.mm.stop_sampling("camera")
        self.mm.start_sampling("camera")
        self.frame(1, previous_timestamp)
        self.frame(99)
        self.mm.data_queue.join()
        release.set()

        event, key = self.output.get(timeout=2)
        self.assertEqual(("camera", 99), (key, event["frame_index"]))
        self.assertTrue(self.output.empty())

    def test_failed_sampling_start_removes_preview_state(self):
        self.mm.stop_sampling("camera")
        self.devices["camera"].start_sampling.side_effect = OSError("start failed")
        with self.assertRaisesRegex(OSError, "start failed"):
            self.mm.start_sampling("camera")

        image = memoryview(b"\x40" * (16 * 12)).cast("B", shape=(12, 16))
        self.assertFalse(self.preview.offer_frame("camera", CameraFrame(image, 0), get_counter_ns(), self.settings))


if __name__ == "__main__":
    unittest.main()
