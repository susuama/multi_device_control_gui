"""測定メタデータの収集と、記録開始・終了日時の回帰テスト。"""

import json
import logging
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.models.measurement import DeviceData
from backend.app.services.device_manager import DeviceManager
from backend.app.services.measurement_manager import MeasurementManager, Metadata
from backend.app.utils.time_utils import get_counter_ns


class MetadataTests(unittest.TestCase):
    def setUp(self):
        previous_logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous_logging_disable)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="metadata-tests-")))
        self.dm = DeviceManager([DummyPowerMeter])
        self.mm = MeasurementManager(self.dm)
        self.releases = []
        self.workers = []
        # 一時フォルダを削除する前に、取得・記録・処理スレッドを終了する。
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

    def connect_and_sample(self):
        self.dm.scan()
        device_id = next(iter(self.dm.get_all()))
        self.dm.connect(device_id)
        self.mm.start_sampling(device_id)
        return device_id

    def wait_until(self, condition):
        deadline = time.monotonic() + 2
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(condition())

    def read_metadata(self, name):
        path = self.directory / name / "metadata.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_idle_metadata_has_no_fabricated_dates_or_recording_path(self):
        metadata = self.mm.get_metadata()
        self.assertIsInstance(metadata, Metadata)
        self.assertIsNone(metadata.name)
        self.assertIsNone(metadata.path)
        self.assertIsNone(metadata.measurement_started_at)
        self.assertIsNone(metadata.measurement_start_counter_ns)
        self.assertIsNone(metadata.recording_started_at)
        self.assertIsNone(metadata.recording_stopped_at)
        self.assertEqual({}, metadata.devices)

    def test_only_sampling_devices_have_history_and_getter_returns_an_independent_copy(self):
        device_id = self.connect_and_sample()
        self.mm.data_queue.join()
        self.mm.start_recording(str(self.directory), "snapshot")
        metadata = self.mm.get_metadata()
        self.assertEqual({device_id}, set(metadata.devices))
        self.assertEqual(1, len(metadata.devices[device_id]))
        self.assertEqual(self.mm._start_datetime, metadata.measurement_started_at)
        self.assertEqual(self.mm._start_counter_ns, metadata.measurement_start_counter_ns)
        initial = metadata.devices[device_id][0]
        self.assertEqual(device_id, initial["info"]["id"])
        settings = self.dm.get_settings(device_id)
        original_wavelength = settings.wavelength
        self.assertEqual(original_wavelength, initial["settings"]["wavelength"])

        # 設定モデルと返却した履歴を変更し、保持中の履歴が変わらないことを確認する。
        settings.wavelength = 600.0
        self.assertEqual(original_wavelength, initial["settings"]["wavelength"])
        initial["settings"]["wavelength"] = 700.0
        initial["info"]["model"] = "modified"
        current = self.mm.get_metadata()
        self.assertEqual(original_wavelength, current.devices[device_id][0]["settings"]["wavelength"])
        self.assertNotEqual("modified", current.devices[device_id][0]["info"]["model"])
        self.assertEqual(current, self.mm.get_metadata())

    def test_add_can_run_before_getter_and_copies_nested_input_values(self):
        info = {"id": "sensor", "details": {"model": "test"}}
        settings = {"channels": [1, 2]}
        with patch.object(self.dm, "get", side_effect=AssertionError("unexpected device lookup")):
            self.mm._add_device_metadata("sensor", 0, info=info, settings=settings)
            settings["channels"].append(3)
            self.mm._add_device_metadata("sensor", 10, info=info, settings=settings)
        info["details"]["model"] = "changed"
        settings["channels"].clear()

        history = self.mm.get_metadata().devices["sensor"]
        self.assertEqual([0, 10], [entry["timestamp_ns"] for entry in history])
        self.assertEqual([[1, 2], [1, 2, 3]], [entry["settings"]["channels"] for entry in history])
        self.assertTrue(all(entry["info"]["details"]["model"] == "test" for entry in history))

    def test_new_sampling_device_and_settings_changes_append_to_their_own_history(self):
        first_id = self.connect_and_sample()
        self.mm.data_queue.join()
        self.mm.start_recording(str(self.directory), "device_history")
        initial = self.mm.get_metadata().devices[first_id][0]
        settings = self.dm.get_settings(first_id).model_copy(update={"wavelength": 650.0})
        self.dm.set_settings(first_id, settings)
        self.dm.set_settings(first_id, settings.model_copy(update={"wavelength": 700.0}))

        second_id = next(device_id for device_id in self.dm.get_all() if device_id != first_id)
        self.dm.connect(second_id)
        self.mm.start_sampling(second_id)
        self.mm.data_queue.join()

        metadata = self.mm.get_metadata()
        self.assertEqual({first_id, second_id}, set(metadata.devices))
        self.assertEqual(initial, metadata.devices[first_id][0])
        self.assertEqual([550.0, 650.0, 700.0], [entry["settings"]["wavelength"] for entry in metadata.devices[first_id]])
        self.assertEqual(1, len(metadata.devices[second_id]))
        latest = self.mm.latest_settings[first_id]
        self.assertEqual(latest.timestamp_ns, metadata.devices[first_id][-1]["timestamp_ns"])
        self.assertEqual(metadata.devices, self.read_metadata("device_history")["devices"])

    def test_queued_settings_preserve_notification_values_and_timestamps(self):
        device_id = self.connect_and_sample()
        self.mm.data_queue.join()
        self.mm.start_recording(str(self.directory), "queued_settings")
        first_time = get_counter_ns()
        messages = [
            DeviceData(device_id, "settings", first_time, {"wavelength": 600.0}),
            DeviceData(device_id, "settings", first_time + 1, {"wavelength": 650.0}),
        ]
        # デバイスの現在値を変更し、過去の通知が現在値で置き換わらないことを確認する。
        device = self.dm.get(device_id)
        device.settings.wavelength = 800.0
        with patch.object(device, "get_settings", side_effect=AssertionError("unexpected settings read")):
            for message in messages:
                self.mm.data_queue.put(message)
            self.mm.data_queue.join()

        messages[0].data["wavelength"] = 900.0
        history = self.mm.get_metadata().devices[device_id]
        self.assertEqual([550.0, 600.0, 650.0], [entry["settings"]["wavelength"] for entry in history])
        self.assertEqual([first_time, first_time + 1], [entry["timestamp_ns"] for entry in history[1:]])
        self.assertEqual(history, self.read_metadata("queued_settings")["devices"][device_id])

    def test_recording_dates_are_stable_and_reset_for_next_recording(self):
        self.connect_and_sample()
        self.mm.start_recording(str(self.directory), "run_01")
        started = self.mm.get_metadata()
        self.assertEqual("run_01", started.name)
        self.assertEqual(str(self.directory / "run_01"), started.path)
        self.assertIsNotNone(started.recording_started_at.tzinfo)
        self.assertGreaterEqual(started.recording_started_at, started.measurement_started_at)
        self.assertIsNone(started.recording_stopped_at)
        self.assertEqual(started.recording_started_at, self.mm.get_metadata().recording_started_at)
        saved = self.read_metadata("run_01")
        self.assertEqual(started.recording_started_at, datetime.fromisoformat(saved["recording_started_at"]))
        self.assertEqual(started.measurement_start_counter_ns, saved["measurement_start_counter_ns"])
        self.assertIsNone(saved["recording_stopped_at"])

        self.mm.stop_recording()
        stopped = self.mm.get_metadata()
        self.assertGreaterEqual(stopped.recording_stopped_at, stopped.recording_started_at)
        self.assertEqual(stopped.recording_stopped_at.isoformat(), self.read_metadata("run_01")["recording_stopped_at"])
        self.mm.stop_recording()
        self.assertEqual(stopped.recording_stopped_at, self.mm.get_metadata().recording_stopped_at)

        self.mm.start_recording(str(self.directory), "run_02")
        restarted = self.mm.get_metadata()
        self.assertEqual("run_02", restarted.name)
        self.assertGreaterEqual(restarted.recording_started_at, stopped.recording_stopped_at)
        self.assertIsNone(restarted.recording_stopped_at)

    def test_collection_failures_report_device_and_do_not_append_partial_entries(self):
        device_id = self.connect_and_sample()
        self.mm.data_queue.join()
        original = self.mm.get_metadata().devices
        device = self.dm.get(device_id)
        for method in ("get_info", "get_settings"):
            with self.subTest(method=method):
                with patch.object(device, method, side_effect=RuntimeError("read failed")):
                    with self.assertRaisesRegex(RuntimeError, device_id) as caught:
                        self.mm._add_device_metadata(device_id, get_counter_ns())
                    self.assertIsInstance(caught.exception.__cause__, RuntimeError)
                self.assertEqual(original, self.mm.get_metadata().devices)
        with self.assertRaisesRegex(RuntimeError, "missing_device") as caught:
            self.mm._add_device_metadata("missing_device", get_counter_ns())
        self.assertIsInstance(caught.exception.__cause__, KeyError)
        self.assertTrue(self.mm.is_sampling(device_id))

    def test_recordings_use_latest_settings_and_keep_histories_separate(self):
        device_id = self.connect_and_sample()
        settings = self.dm.get_settings(device_id)
        self.dm.set_settings(device_id, settings.model_copy(update={"wavelength": 600.0}))
        self.mm.data_queue.join()
        self.assertEqual({}, self.mm.get_metadata().devices)

        self.mm.start_recording(str(self.directory), "first")
        self.dm.set_settings(device_id, settings.model_copy(update={"wavelength": 650.0}))
        self.mm.stop_recording()
        first_path = self.directory / "first" / "metadata.json"
        first_contents = first_path.read_bytes()
        first_history = self.read_metadata("first")["devices"][device_id]
        self.assertEqual([600.0, 650.0], [entry["settings"]["wavelength"] for entry in first_history])

        # 記録外の変更と停止済みデバイスの通知を受信し、次の記録を開始する。
        self.dm.set_settings(device_id, settings.model_copy(update={"wavelength": 700.0}))
        other_id = next(value for value in self.dm.get_all() if value != device_id)
        self.dm.connect(other_id)
        self.mm.start_sampling(other_id)
        self.mm.stop_sampling(other_id)
        self.mm.data_queue.join()
        self.assertEqual(first_contents, first_path.read_bytes())

        self.mm.start_recording(str(self.directory), "second")
        second = self.read_metadata("second")
        self.assertEqual({device_id}, set(second["devices"]))
        self.assertEqual([700.0], [entry["settings"]["wavelength"] for entry in second["devices"][device_id]])
        self.assertIsNone(second["recording_stopped_at"])
        self.assertEqual(first_contents, first_path.read_bytes())

    def test_recording_start_uses_settings_before_the_start_command(self):
        device_id = self.connect_and_sample()
        self.mm.data_queue.join()
        entered = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        self.releases.append(release)
        errors = []
        original_start = self.mm._data_recorder.start

        def delayed_start(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test recording start timed out")
            return original_start(*args, **kwargs)

        def start_recording():
            try:
                self.mm.start_recording(str(self.directory), "ordered_start")
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        with patch.object(self.mm._data_recorder, "start", side_effect=delayed_start):
            worker = threading.Thread(target=start_recording, name="test-metadata-start")
            self.workers.append(worker)
            worker.start()
            self.assertTrue(entered.wait(1))
            # 開始命令の処理を待たせた状態で、後続の設定変更通知をQueueへ追加する。
            settings = self.dm.get_settings(device_id).model_copy(update={"wavelength": 650.0})
            self.dm.set_settings(device_id, settings)
            release.set()
            self.assertTrue(completed.wait(2))
        self.assertEqual([], errors)
        self.mm.data_queue.join()
        history = self.read_metadata("ordered_start")["devices"][device_id]
        self.assertEqual([550.0, 650.0], [entry["settings"]["wavelength"] for entry in history])

    def test_initial_metadata_save_failure_stops_recording_and_reports_error(self):
        device_id = self.connect_and_sample()
        with patch.object(self.mm._data_recorder, "write_metadata", side_effect=OSError("metadata disk full")):
            with self.assertRaisesRegex(OSError, "metadata disk full"):
                self.mm.start_recording(str(self.directory), "failed_start")
        self.assertFalse(self.mm.is_recording())
        self.assertEqual("metadata disk full", self.mm.get_recording_status().error)
        self.assertIsNotNone(self.mm.get_metadata().recording_stopped_at)
        self.assertTrue(self.mm.is_sampling(device_id))
        self.assertTrue(self.mm._processor_thread.is_alive())

        self.mm.start_recording(str(self.directory), "recovered")
        self.assertIsNone(self.mm.get_recording_status().error)
        self.assertIsNone(self.read_metadata("recovered")["recording_stopped_at"])

    def test_failed_metadata_replacement_preserves_file_and_stops_recording(self):
        device_id = self.connect_and_sample()
        self.mm.start_recording(str(self.directory), "replace_failure")
        folder = self.directory / "replace_failure"
        original_contents = (folder / "metadata.json").read_bytes()
        settings = self.dm.get_settings(device_id).model_copy(update={"wavelength": 650.0})

        with patch.object(Path, "replace", side_effect=OSError("metadata replace failed")):
            self.dm.set_settings(device_id, settings)
            self.mm.data_queue.join()

        self.assertFalse(self.mm.is_recording())
        self.assertEqual("metadata replace failed", self.mm.get_recording_status().error)
        self.assertEqual(original_contents, (folder / "metadata.json").read_bytes())
        self.assertEqual([], list(folder.glob(".metadata-*.tmp")))
        self.assertTrue(self.mm.is_sampling(device_id))
        self.assertTrue(self.mm._processor_thread.is_alive())

    def test_write_failure_and_shutdown_record_stop_dates(self):
        device_id = self.connect_and_sample()
        self.mm.start_recording(str(self.directory), "write_failure")
        with patch.object(self.mm._data_recorder, "write") as write:
            def fail(*args, **kwargs):
                self.mm._data_recorder.stop()
                raise OSError("disk full")
            write.side_effect = fail
            self.wait_until(lambda: self.mm.get_metadata().recording_stopped_at is not None)
            self.mm.data_queue.join()
        self.assertFalse(self.mm.is_recording())
        self.assertTrue(self.mm.is_sampling(device_id))
        stopped = self.mm.get_metadata()
        self.assertEqual(stopped.recording_stopped_at.isoformat(), self.read_metadata("write_failure")["recording_stopped_at"])

        self.mm.start_recording(str(self.directory), "shutdown")
        self.mm.shutdown()
        metadata = self.mm.get_metadata()
        self.assertIsNotNone(metadata.recording_started_at)
        self.assertIsNotNone(metadata.recording_stopped_at)
        self.assertGreaterEqual(metadata.recording_stopped_at, metadata.recording_started_at)
        saved = self.read_metadata("shutdown")
        self.assertEqual(metadata.recording_stopped_at.isoformat(), saved["recording_stopped_at"])
        self.assertEqual(metadata.measurement_started_at.isoformat(), saved["measurement_started_at"])
        self.assertIsNone(self.mm._processor_thread)


if __name__ == "__main__":
    unittest.main()
