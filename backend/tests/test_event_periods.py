"""QueueとJPEG変換を通じた測定期間の引き継ぎを検証する。"""

import logging
import queue
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, call, patch

from backend.app.models.devices import CameraSetting, DeviceInfo
from backend.app.models.measurement import CameraFrame, DeviceData, MeasurementPeriod
from backend.app.services.camera_preview import CameraPreviewService
from backend.app.services.measurement_manager import MeasurementManager, _SamplingData, _SamplingQueue
from backend.app.utils.time_utils import get_counter_ns


class SamplingQueueTests(unittest.TestCase):
    def setUp(self):
        self.on_queue_full = Mock()
        self.period = MeasurementPeriod("period-A", datetime.fromisoformat("2026-11-01T00:00:00+09:00"), 100)

    def test_period_and_start_result_are_attached_before_and_after_start(self):
        output = queue.Queue()
        source = _SamplingQueue(output, self.period, on_queue_full=self.on_queue_full, on_error=lambda *_: None)
        data = DeviceData("meter", "sample", 200, {"power_w": 1.0})
        source.put(data)
        source.accepted = True
        source.ready.set()
        source.put(data)
        self.assertEqual(2, output.qsize())
        self.on_queue_full.assert_not_called()
        for _ in range(2):
            item = output.get_nowait()
            self.assertIsInstance(item, _SamplingData)
            self.assertIs(data, item.data)
            self.assertIs(source, item.source)
            self.assertIs(self.period, item.source.period)

    def test_full_queue_keeps_one_slot_per_item_and_preserves_put_options(self):
        output = queue.Queue(maxsize=1)
        source = _SamplingQueue(output, self.period, on_queue_full=self.on_queue_full, on_error=lambda *_: None)
        data = DeviceData("meter", "sample", 200, {})
        source.put(data, block=False)
        with self.assertRaisesRegex(queue.Full, "sample was not queued"):
            source.put(data, block=False)
        with self.assertRaisesRegex(queue.Full, "sample was not queued"):
            source.put(data, timeout=0.01)
        self.assertEqual(1, output.qsize())
        self.assertEqual([call("meter"), call("meter")], self.on_queue_full.call_args_list)

    def test_failed_start_rejects_later_data(self):
        output = queue.Queue()
        source = _SamplingQueue(output, self.period, on_queue_full=self.on_queue_full, on_error=lambda *_: None)
        source.ready.set()
        with self.assertRaisesRegex(RuntimeError, "did not start"):
            source.put(DeviceData("meter", "sample", 200, {}))
        self.assertTrue(output.empty())
        self.on_queue_full.assert_not_called()


class EventPeriodTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.output = queue.Queue()
        self.events = Mock()
        self.events.is_running.return_value = True
        self.events.publish.side_effect = lambda event, *, key: self.output.put((event, key))
        self.preview = CameraPreviewService(self.events)
        self.sources = {}
        self.devices = {}
        self.settings = CameraSetting(width=8, height=6, fps=10, exposure=0.01, gain=1)
        for device_id, kind in (("meter", "power_meter"), ("other", "power_meter"), ("camera", "camera")):
            device = Mock()
            device.is_sampling.return_value = False
            device.get_info.return_value = DeviceInfo(
                id=device_id, type=kind, manufacturer="test", model="test", serial_number=device_id,
                address=device_id, status="connected",
            )
            settings = self.settings.model_dump() if kind == "camera" else {"wavelength": 550.0}

            def start(source, device=device, device_id=device_id, settings=settings):
                self.sources[device_id] = source
                device.is_sampling.return_value = True
                source.put(DeviceData(device_id, "settings", get_counter_ns(), settings))

            device.start_sampling.side_effect = start
            device.stop_sampling.side_effect = lambda device=device: setattr(device.is_sampling, "return_value", False)
            self.devices[device_id] = device
        manager = Mock()
        manager.get_all.return_value = self.devices
        manager.get.side_effect = self.devices.__getitem__
        self.mm = MeasurementManager(manager, self.events, self.preview, server_instance_id="server-A")
        self.releases = []
        self.addCleanup(self.finish)

    def finish(self):
        for release in self.releases:
            release.set()
        try:
            self.mm.shutdown()
        finally:
            self.preview.shutdown()

    def sample(self, device_id="meter", *, source=None, timestamp=None):
        (source or self.sources[device_id]).put(DeviceData(
            device_id, "sample", timestamp if timestamp is not None else get_counter_ns(), {"power_w": 1.0},
        ))

    def frame(self, index):
        image = memoryview(bytes(range(48))).cast("B", shape=(6, 8))
        self.sources["camera"].put(DeviceData("camera", "frame", get_counter_ns(), CameraFrame(image, index)))

    def test_numeric_events_share_period_across_devices_and_change_after_restart(self):
        self.mm.start_sampling("meter")
        first = self.mm.get_measurement_status()
        self.mm.start_sampling("other")
        for device_id in ("meter", "other"):
            self.sample(device_id)
            event, key = self.output.get(timeout=1)
            self.assertEqual(None, key)
            self.assertEqual("server-A", event["server_instance_id"])
            self.assertEqual(first.measurement_id, event["measurement_id"])
        self.mm.stop_all_sampling()
        self.mm.start_sampling("meter")
        current = self.mm.get_measurement_status()
        self.assertNotEqual(first.measurement_id, current.measurement_id)
        self.sample()
        event, _ = self.output.get(timeout=1)
        self.assertEqual(current.measurement_id, event["measurement_id"])

    def test_old_source_keeps_its_id_and_time_basis_after_new_period_starts(self):
        self.mm.start_sampling("meter")
        old_source = self.sources["meter"]
        old_period = old_source.period
        timestamp = old_period.start_counter_ns + 1_000_000_000
        self.mm.stop_all_sampling()
        self.mm.start_sampling("meter")
        self.assertNotEqual(old_period.measurement_id, self.mm.get_measurement_status().measurement_id)
        # 古い投入元で遅れて完了したputを再現し、処理時の現在値で補われないことを確認する。
        self.sample(source=old_source, timestamp=timestamp)
        event, _ = self.output.get(timeout=1)
        self.assertEqual(old_period.measurement_id, event["measurement_id"])
        self.assertEqual(1.0, event["elapsed_s"])
        self.assertEqual((old_period.started_at + timedelta(seconds=1)).isoformat(), event["datetime"])

    def test_failed_start_does_not_publish_early_samples(self):
        def fail(source, **kwargs):
            source.put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 1.0}))
            raise OSError("start failed")

        self.devices["meter"].start_sampling.side_effect = fail
        with self.assertRaisesRegex(OSError, "start failed"):
            self.mm.start_sampling("meter")
        self.mm.data_queue.join()
        self.assertTrue(self.output.empty())
        self.assertIsNone(self.mm.get_measurement_status().measurement_id)

    def restart_camera_during_conversion(self, *, keep_period):
        if keep_period:
            self.mm.start_sampling("meter")
        self.mm.start_sampling("camera")
        first = self.mm.get_measurement_status()
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        encode = self.preview._encode_jpeg

        def delayed(frame):
            if frame.frame_index == 0:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("Test did not release JPEG conversion.")
            return encode(frame)

        with patch.object(self.preview, "_encode_jpeg", side_effect=delayed):
            self.frame(0)
            self.assertTrue(entered.wait(1))
            self.mm.stop_sampling("camera")
            self.mm.start_sampling("camera")
            current = self.mm.get_measurement_status()
            self.assertEqual(keep_period, first.measurement_id == current.measurement_id)
            self.frame(1)
            self.mm.data_queue.join()
            release.set()
            event, key = self.output.get(timeout=1)
        self.assertEqual("camera", key)
        self.assertEqual(1, event["frame_index"])
        self.assertEqual("server-A", event["server_instance_id"])
        self.assertEqual(current.measurement_id, event["measurement_id"])
        self.assertTrue(self.output.empty())

    def test_individual_camera_restart_discards_old_jpeg_with_same_measurement_id(self):
        self.restart_camera_during_conversion(keep_period=True)

    def test_new_measurement_discards_old_jpeg_and_publishes_new_id(self):
        self.restart_camera_during_conversion(keep_period=False)


if __name__ == "__main__":
    unittest.main()
