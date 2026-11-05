"""共通Queueの取得データ・設定通知と、通知順序の回帰テスト。"""

import csv
import logging
import queue
import tempfile
import threading
import time
import unittest
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.models.measurement import DeviceData
from backend.app.services.device_manager import DeviceManager
from backend.app.services.event_broadcaster import EventBroadcaster
from backend.app.services.measurement_manager import MeasurementManager
from backend.app.utils.time_utils import get_counter_ns


class DeviceDataTests(unittest.TestCase):
    def setUp(self):
        previous_logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous_logging_disable)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="device-data-tests-")))
        self.dm = DeviceManager([DummyPowerMeter])
        self.mm = MeasurementManager(self.dm, EventBroadcaster())
        self.dm.scan()
        self.device_id = next(iter(self.dm.get_all()))
        self.device = self.dm.get(self.device_id)
        self.dm.connect(self.device_id)
        self.releases = []
        self.workers = []
        self.addCleanup(self.shutdown)

    def shutdown(self):
        for release in self.releases:
            release.set()
        for worker in self.workers:
            worker.join(2)
        try:
            self.mm.shutdown()
        finally:
            self.dm.shutdown()
        self.assertIsNone(self.mm._processor_thread)

    def wait_until(self, condition):
        deadline = time.monotonic() + 2
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_read_returns_four_common_fields_and_device_specific_payload(self):
        sample = self.dm.read(self.device_id)
        self.assertIsInstance(sample, DeviceData)
        self.assertEqual(["id", "type", "timestamp_ns", "data"], [field.name for field in fields(sample)])
        self.assertEqual(self.device_id, sample.id)
        self.assertEqual("sample", sample.type)
        self.assertIsInstance(sample.timestamp_ns, int)
        self.assertIsInstance(sample.data["power_w"], float)

    def test_start_sends_current_settings_before_first_sample(self):
        data_queue = queue.Queue()
        self.device.start_sampling(data_queue)
        settings = data_queue.get(timeout=1)
        sample = data_queue.get(timeout=1)
        self.assertEqual("settings", settings.type)
        self.assertEqual(self.device.get_settings().model_dump(mode="json"), settings.data)
        self.assertEqual("sample", sample.type)
        self.assertLessEqual(settings.timestamp_ns, sample.timestamp_ns)

    def test_device_operations_keep_applied_settings_and_wrap_measurements(self):
        self.device.disconnect()
        initial = self.device.get_settings().model_copy(update={"wavelength": 600.0})
        applied = initial.model_copy(update={"wavelength": 650.0})
        with patch.object(self.device, "_open_device", return_value=initial) as open_device, \
                patch.object(self.device, "_apply_settings", return_value=applied), \
                patch.object(self.device, "_read_sample", return_value={"power_w": 1.25}), \
                patch.object(self.device, "_close_device") as close_device:
            self.assertTrue(self.device.connect())
            self.assertEqual(initial, self.device.get_settings())
            self.assertTrue(self.device.connect())
            open_device.assert_called_once_with()
            result = self.device.set_settings(initial.model_dump() | {"wavelength": 649.9})
            self.assertEqual(applied, result)
            self.assertEqual(applied, self.device.get_settings())
            before = get_counter_ns()
            sample = self.device.read()
            self.assertEqual(self.device_id, sample.id)
            self.assertEqual("sample", sample.type)
            self.assertEqual({"power_w": 1.25}, sample.data)
            self.assertLessEqual(before, sample.timestamp_ns)
            self.assertLessEqual(sample.timestamp_ns, get_counter_ns())
            self.assertTrue(self.device.disconnect())
            close_device.assert_called_once_with()

    def test_failed_device_apply_preserves_settings_without_success_notification(self):
        original = self.device.get_settings().model_dump()
        data_queue = queue.Queue()
        self.device.start_sampling(data_queue)
        self.assertEqual("settings", data_queue.get(timeout=1).type)
        with patch.object(self.device, "_apply_settings", side_effect=OSError("apply failed")):
            with self.assertRaisesRegex(OSError, "apply failed"):
                self.device.set_settings(original | {"wavelength": 650.0})
        self.device.stop_sampling()
        self.assertEqual(original, self.device.get_settings().model_dump())
        while not data_queue.empty():
            self.assertEqual("sample", data_queue.get_nowait().type)

    def test_idle_changes_are_sent_at_next_start_and_stop_detaches_queue(self):
        settings = self.device.get_settings().model_copy(update={"wavelength": 600.0})
        self.mm.set_settings(self.device_id, settings)
        self.assertIsNone(self.device.data_queue)
        data_queue = queue.Queue()
        self.device.start_sampling(data_queue)
        self.assertEqual(600.0, data_queue.get(timeout=1).data["wavelength"])
        self.device.stop_sampling()
        self.assertIsNone(self.device.data_queue)
        while not data_queue.empty():
            data_queue.get_nowait()
        self.mm.set_settings(self.device_id, settings.model_copy(update={"wavelength": 650.0}))
        self.assertTrue(data_queue.empty())

    def test_settings_notification_is_independent_of_later_model_changes(self):
        data_queue = queue.Queue()
        self.device.start_sampling(data_queue)
        settings = self.device.get_settings().model_copy(update={"wavelength": 650.0})
        self.mm.set_settings(self.device_id, settings)
        settings.wavelength = 700.0
        self.device.stop_sampling()
        messages = []
        while not data_queue.empty():
            messages.append(data_queue.get_nowait())
        notifications = [message for message in messages if message.type == "settings"]
        self.assertEqual([550.0, 650.0], [message.data["wavelength"] for message in notifications])

    def test_read_and_settings_notification_keep_queue_order(self):
        data_queue = queue.Queue()
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        self.releases.append(release)
        errors = []
        new_settings = self.device.get_settings().model_copy(update={"wavelength": 650.0})

        def read():
            if not entered.is_set():
                entered.set()
                release.wait(2)
            return DeviceData(self.device_id, "sample", get_counter_ns(), {"power_w": self.device.settings.wavelength})

        def change_settings():
            try:
                self.mm.set_settings(self.device_id, new_settings)
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        with patch.object(self.device, "read", side_effect=read):
            self.device.start_sampling(data_queue)
            self.assertTrue(entered.wait(1))
            worker = threading.Thread(target=change_settings, name="test-settings-change")
            self.workers.append(worker)
            worker.start()
            self.assertFalse(completed.wait(0.03))
            release.set()
            self.assertTrue(completed.wait(1))
            self.assertEqual([], errors)
            messages = []
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                message = data_queue.get(timeout=1)
                messages.append(message)
                if message.type == "sample" and message.data["power_w"] == 650.0:
                    break
            self.device.stop_sampling()

        old_sample = next(index for index, message in enumerate(messages) if message.type == "sample" and message.data["power_w"] == 550.0)
        changed = next(index for index, message in enumerate(messages) if message.type == "settings" and message.data["wavelength"] == 650.0)
        new_sample = next(index for index, message in enumerate(messages) if message.type == "sample" and message.data["power_w"] == 650.0)
        self.assertLess(old_sample, changed)
        self.assertLess(changed, new_sample)
        self.assertLessEqual(messages[old_sample].timestamp_ns, messages[changed].timestamp_ns)
        self.assertLessEqual(messages[changed].timestamp_ns, messages[new_sample].timestamp_ns)

    def test_processor_receives_setting_values_and_times_without_writing_settings_to_csv(self):
        self.mm.start_sampling(self.device_id)
        self.wait_until(lambda: self.device_id in self.mm.latest_settings)
        initial = self.mm.latest_settings[self.device_id]
        self.mm.start_recording(str(self.directory), "run_01")
        settings = self.device.get_settings().model_copy(update={"wavelength": 650.0})
        self.mm.set_settings(self.device_id, settings)
        self.wait_until(lambda: self.mm.latest_settings[self.device_id].data["wavelength"] == 650.0)
        notification = self.mm.latest_settings[self.device_id]
        self.assertGreaterEqual(notification.timestamp_ns, initial.timestamp_ns)
        self.assertGreaterEqual(self.mm._get_elapsed(notification.timestamp_ns), 0)
        self.wait_until(lambda: bool(self.mm._data_recorder._csv_writers[self.device_id].fieldnames))
        self.mm.stop_sampling(self.device_id)
        self.mm.stop_recording()

        path = self.directory / "run_01" / f"{self.device_id}.csv"
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            self.assertEqual(["id", "timestamp_ns", "power_w", "elapsed_s"], reader.fieldnames)
            self.assertGreater(len(list(reader)), 0)

    def test_processor_and_csv_handle_non_power_payload(self):
        def read_temperature():
            return DeviceData(self.device_id, "sample", get_counter_ns(), {"temperature_c": 23.5})

        with patch.object(self.device, "read", side_effect=read_temperature):
            self.mm.start_sampling(self.device_id)
            self.mm.start_recording(str(self.directory), "generic")
            self.wait_until(lambda: bool(self.mm._data_recorder._csv_writers[self.device_id].fieldnames))
            self.mm.stop_sampling(self.device_id)
            self.mm.stop_recording()
        with (self.directory / "generic" / f"{self.device_id}.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.DictReader(file))
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(all(row["temperature_c"] == "23.5" for row in rows))
        self.assertNotIn("power_w", rows[0])


if __name__ == "__main__":
    unittest.main()
