"""記録フォルダ・CSV・記録APIの回帰テスト。出力は一時フォルダに限定する。"""

import csv
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models.measurement import DeviceData
from backend.app.models.recordings import RecordingTarget
from backend.app.services.data_racorder import DataRecorder


def numeric_targets(*device_ids):
    """数値記録のテストで使う、IDごとの記録対象を作成する。"""
    return {device_id: RecordingTarget(type="power_meter", settings={}) for device_id in device_ids}


class DataRecorderTests(unittest.TestCase):
    def setUp(self):
        previous_logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous_logging_disable)
        self.parent = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="data-recorder-tests-")))
        self.recorder = DataRecorder()
        # CSVを閉じてから一時フォルダを削除する。
        self.addCleanup(self.stop_recorder)

    def stop_recorder(self):
        self.recorder.stop()
        self.recorder.wait_stopped()

    def read_rows(self, path):
        with path.open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))

    def test_start_creates_recording_folder_and_serializable_status(self):
        self.assertIsNone(self.recorder.get_status().directory)
        self.recorder.start(str(self.parent), "run_01", targets=numeric_targets("pm_1", "pm_2"))

        status = self.recorder.get_status()
        self.assertTrue((self.parent / "run_01").is_dir())
        self.assertTrue(status.is_recording)
        self.assertEqual(str(self.parent / "run_01"), status.directory)
        self.assertEqual("run_01", status.name)
        self.assertEqual(status.model_dump(), status.model_dump(mode="json"))
        self.assertEqual({"pm_1", "pm_2"}, set(self.recorder._csv_writers))
        for device_id in ("pm_1", "pm_2"):
            self.assertTrue(self.recorder.is_recording_device(device_id))
            self.assertEqual([], self.recorder._csv_writers[device_id].fieldnames)
            self.assertEqual(b"", (self.parent / "run_01" / f"{device_id}.csv").read_bytes())
        self.recorder.stop()
        self.recorder.wait_stopped()
        self.assertFalse(self.recorder.is_recording_device("pm_1"))
        self.assertEqual(b"", (self.parent / "run_01" / "pm_2.csv").read_bytes())

    def test_consecutive_recordings_keep_data_separate(self):
        for name, power in (("run_01", 0.1), ("run_02", 0.2)):
            self.recorder.start(str(self.parent), name, targets=numeric_targets("pm_1",))
            self.recorder.write(DeviceData("pm_1", "sample", 100, {"power_w": power}), elapsed_s=0.0)
            self.recorder.stop()
            self.recorder.wait_stopped()

        self.assertFalse((self.parent / "pm_1.csv").exists())
        self.assertEqual("0.1", self.read_rows(self.parent / "run_01" / "pm_1.csv")[0]["power_w"])
        self.assertEqual("0.2", self.read_rows(self.parent / "run_02" / "pm_1.csv")[0]["power_w"])

    def test_existing_recording_folder_is_not_reused(self):
        self.recorder.start(str(self.parent), "run_01", targets=numeric_targets("pm_1",))
        self.recorder.write(DeviceData("pm_1", "sample", 100, {"power_w": 0.1}))
        self.recorder.stop()
        self.recorder.wait_stopped()
        path = self.parent / "run_01" / "pm_1.csv"
        original = path.read_bytes()

        with self.assertRaises(FileExistsError):
            self.recorder.start(str(self.parent), "run_01", targets=numeric_targets("pm_1",))

        self.assertFalse(self.recorder.is_recording())
        self.assertTrue(self.recorder.error)
        self.assertEqual(original, path.read_bytes())

    def test_recording_name_cannot_escape_parent_folder(self):
        for name in ("..", "../outside", str(self.parent / "outside")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.recorder.start(str(self.parent), name, targets=numeric_targets())
        self.assertFalse(self.recorder.is_recording())
        self.assertEqual([], list(self.parent.iterdir()))

    def test_per_device_csv_appends_rows_and_preserves_zero_elapsed(self):
        self.recorder.start(str(self.parent), "run_01", targets=numeric_targets("pm_1", "pm_2"))
        self.recorder.write(DeviceData("pm_1", "sample", 100, {"power_w": 0.1}), elapsed_s=0.0)
        self.recorder.write(DeviceData("pm_1", "sample", 200, {"power_w": 0.2}), elapsed_s=0.1)
        self.recorder.write(DeviceData("pm_2", "sample", 150, {"power_w": 0.3}), elapsed_s=0.05)
        self.recorder.stop()
        self.recorder.wait_stopped()

        first_path = self.parent / "run_01" / "pm_1.csv"
        rows = self.read_rows(first_path)
        self.assertTrue(first_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(2, len(rows))
        self.assertEqual(["pm_1", "pm_1"], [row["id"] for row in rows])
        self.assertEqual("0.0", rows[0]["elapsed_s"])
        self.assertEqual("pm_2", self.read_rows(self.parent / "run_01" / "pm_2.csv")[0]["id"])

    def test_filename_collisions_keep_all_devices_separate(self):
        ids = ["pm/a", "pm:a", "PM_A", "pm_a_2"]
        self.recorder.start(str(self.parent), "run_01", targets=numeric_targets(*ids))
        for device_id in ids:
            self.recorder.write(DeviceData(device_id, "sample", 100, {"power_w": 0.1}))
            self.recorder.write(DeviceData(device_id, "sample", 200, {"power_w": 0.2}))
        self.assertTrue(self.recorder.is_recording())
        self.recorder.stop()
        self.recorder.wait_stopped()

        paths = list((self.parent / "run_01").glob("*.csv"))
        self.assertEqual(len(ids), len(paths))
        saved_ids = []
        for path in paths:
            rows = self.read_rows(path)
            self.assertEqual(2, len(rows))
            self.assertEqual(rows[0]["id"], rows[1]["id"])
            saved_ids.append(rows[0]["id"])
        self.assertCountEqual(ids, saved_ids)

    def test_start_collision_preserves_existing_csv_and_closes_open_files(self):
        original_open = Path.open
        opened = []

        def open_with_collision(path, mode="r", *args, **kwargs):
            if mode == "x" and path.name == "pm_2.csv":
                # フォルダ作成後に、別の処理が同名ファイルを作成した場合を再現する。
                with original_open(path, "wb") as file:
                    file.write(b"existing measurement")
            file = original_open(path, mode, *args, **kwargs)
            if mode == "x":
                opened.append(file)
            return file

        with patch.object(Path, "open", new=open_with_collision):
            with self.assertRaises(FileExistsError):
                self.recorder.start(str(self.parent), "run_01", targets=numeric_targets("pm_1", "pm_2"))

        with self.assertRaises(FileExistsError):
            self.recorder.wait_stopped()

        self.assertEqual(b"existing measurement", (self.parent / "run_01" / "pm_2.csv").read_bytes())
        self.assertEqual(1, len(opened))
        self.assertFalse(self.recorder.is_recording())
        self.assertTrue(self.recorder.error)
        self.assertTrue(all(file.closed for file in opened))
        self.assertEqual({}, self.recorder._files)
        self.assertEqual({}, self.recorder._csv_writers)
        self.assertFalse(self.recorder.is_recording_device("pm_1"))
        self.assertTrue((self.parent / "run_01" / "pm_1.csv").exists())

        self.recorder.start(str(self.parent), "recovered", targets=numeric_targets("pm_2",))
        self.assertIsNone(self.recorder.error)
        self.assertEqual({"pm_2"}, set(self.recorder._csv_writers))

    def test_writer_creation_failure_closes_every_open_file(self):
        original_writer = csv.DictWriter
        opened = []

        def create_writer(file, **kwargs):
            opened.append(file)
            if len(opened) == 2:
                raise OSError("writer creation failed")
            return original_writer(file, **kwargs)

        with patch("backend.app.services.data_racorder.csv.DictWriter", side_effect=create_writer):
            with self.assertRaisesRegex(OSError, "writer creation failed"):
                self.recorder.start(str(self.parent), "failed", targets=numeric_targets("pm_1", "pm_2"))
        with self.assertRaisesRegex(OSError, "writer creation failed"):
            self.recorder.wait_stopped()
        self.assertEqual(2, len(opened))
        self.assertTrue(all(file.closed for file in opened))
        self.assertFalse(self.recorder.is_recording())
        self.assertEqual({}, self.recorder._files)
        self.assertEqual({}, self.recorder._csv_writers)

    def test_unknown_device_does_not_create_a_file_or_interrupt_recording(self):
        self.recorder.start(str(self.parent), "fixed", targets=numeric_targets("pm_1",))
        writer = self.recorder._csv_writers["pm_1"]
        self.recorder.write(DeviceData("pm_2", "sample", 100, {"power_w": 0.1}))
        self.assertTrue(self.recorder.is_recording())
        self.assertFalse((self.parent / "fixed" / "pm_2.csv").exists())
        self.assertEqual([], writer.fieldnames)
        self.recorder.write(DeviceData("pm_1", "sample", 200, {"power_w": 0.2}))
        self.assertIs(writer, self.recorder._csv_writers["pm_1"])
        self.recorder.stop()
        self.recorder.wait_stopped()
        self.assertEqual("0.2", self.read_rows(self.parent / "fixed" / "pm_1.csv")[0]["power_w"])

    def test_header_is_written_once_and_values_follow_initial_column_order(self):
        self.recorder.start(str(self.parent), "columns", targets=numeric_targets("sensor",))
        self.recorder.write(DeviceData("sensor", "sample", 100, {"temperature_c": 20, "humidity": 30}))
        self.recorder.write(DeviceData("sensor", "sample", 200, {"humidity": 40, "temperature_c": 21}))
        self.recorder.stop()
        self.recorder.wait_stopped()
        with (self.parent / "columns" / "sensor.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
        self.assertEqual([
            ["id", "timestamp_ns", "temperature_c", "humidity"],
            ["sensor", "100", "20", "30"],
            ["sensor", "200", "21", "40"],
        ], rows)

    def test_new_column_raises_and_closes_files(self):
        self.recorder.start(str(self.parent), "columns", targets=numeric_targets("pm_1",))
        self.recorder.write(DeviceData("pm_1", "sample", 100, {"power_w": 0.1}))
        opened = list(self.recorder._files.values())
        with self.assertRaises(ValueError):
            self.recorder.write(DeviceData("pm_1", "sample", 200, {"power_w": 0.2, "extra": 1}))
        with self.assertRaises(ValueError):
            self.recorder.wait_stopped()
        self.assertFalse(self.recorder.is_recording())
        self.assertTrue(all(file.closed for file in opened))
        rows = self.read_rows(self.parent / "columns" / "pm_1.csv")
        self.assertEqual(1, len(rows))
        self.assertEqual("0.1", rows[0]["power_w"])

    def test_missing_writer_for_recording_device_is_an_error(self):
        self.recorder.start(str(self.parent), "missing", targets=numeric_targets("pm_1",))
        opened = list(self.recorder._files.values())
        del self.recorder._csv_writers["pm_1"]
        with self.assertRaises(KeyError):
            self.recorder.write(DeviceData("pm_1", "sample", 100, {"power_w": 0.1}))
        with self.assertRaises(KeyError):
            self.recorder.wait_stopped()
        self.assertFalse(self.recorder.is_recording())
        self.assertTrue(all(file.closed for file in opened))

    def test_metadata_is_saved_and_can_be_finalized_after_stop(self):
        with self.assertRaisesRegex(RuntimeError, "Recording has not started"):
            self.recorder.write_metadata({})
        self.recorder.start(str(self.parent), "metadata", targets=numeric_targets())
        metadata = {"devices": {"pm_1": [{"settings": {"wavelength": 550.0}}]}}
        self.recorder.write_metadata(metadata)
        metadata["devices"]["pm_1"][0]["settings"]["wavelength"] = 650.0
        self.assertEqual(550.0, self.recorder.metadata["devices"]["pm_1"][0]["settings"]["wavelength"])

        self.recorder.stop()

        self.recorder.wait_stopped()
        self.recorder.write_metadata({"recording_stopped_at": "2026-10-19T12:00:00+09:00"})
        path = self.parent / "metadata" / "metadata.json"
        stopped_at = self.recorder.get_recording_stopped_at(str(self.parent / "metadata"))
        self.assertEqual({"recording_stopped_at": stopped_at.isoformat()}, json.loads(path.read_text(encoding="utf-8")))
        self.recorder.start(str(self.parent), "next_metadata", targets=numeric_targets())
        self.assertEqual({}, self.recorder.metadata)

    def test_video_results_survive_final_metadata_writes_and_reset_at_next_start(self):
        self.recorder.start(str(self.parent), "video_results", targets=numeric_targets())
        self.recorder.write_metadata({"name": "video_results"})
        video = Mock()
        video.has_pending_cleanup.return_value = False
        video.get_result.return_value = {"state": "completed", "error": None}
        self.recorder._video_recorders["camera"] = video
        self.recorder.stop()
        self.recorder.wait_stopped()
        video.stop.assert_called_once()
        self.assertEqual({}, self.recorder._video_recorders)
        self.assertEqual({"camera": {"state": "completed", "error": None}}, self.recorder.metadata["video_results"])
        final_metadata = {"name": "video_results", "recording_stopped_at": "test-stop-time"}
        self.recorder.write_metadata(final_metadata)
        self.assertNotIn("video_results", final_metadata)
        self.recorder.stop()
        self.recorder.wait_stopped()
        self.recorder.write_metadata(final_metadata)
        saved = json.loads((self.parent / "video_results" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(self.recorder.get_recording_stopped_at(str(self.parent / "video_results")).isoformat(), saved["recording_stopped_at"])
        self.assertEqual({"camera": {"state": "completed", "error": None}}, saved["video_results"])
        self.recorder.start(str(self.parent), "next_results", targets=numeric_targets())
        self.recorder.write_metadata({"name": "next_results"})
        self.assertNotIn("video_results", self.recorder.metadata)

    def test_failed_video_stop_collects_all_results_and_closes_csv(self):
        self.recorder.start(str(self.parent), "failed_video", targets=numeric_targets("meter"))
        file = self.recorder._files["meter"]
        failed = Mock()
        failed.has_pending_cleanup.return_value = False
        failed.stop.side_effect = RuntimeError("video failed")
        failed.get_result.return_value = {"state": "failed", "error": "video failed"}
        completed = Mock()
        completed.has_pending_cleanup.return_value = False
        completed.get_result.return_value = {"state": "completed", "error": None}
        self.recorder._video_recorders.update({"camera1": failed, "camera2": completed})
        with self.assertRaisesRegex(RuntimeError, "video failed"):
            self.recorder.stop()
            self.recorder.wait_stopped()
        completed.stop.assert_called_once()
        self.assertTrue(file.closed)
        self.assertFalse(self.recorder.is_recording())
        self.assertEqual({}, self.recorder._video_recorders)
        self.recorder.write_metadata({"recording_stopped_at": "test-stop-time"})
        saved = json.loads((self.parent / "failed_video" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual({
            "camera1": {"state": "failed", "error": "video failed"},
            "camera2": {"state": "completed", "error": None},
        }, saved["video_results"])

    def test_failed_final_metadata_write_retains_video_results_for_retry(self):
        self.recorder.start(str(self.parent), "retry_results", targets=numeric_targets())
        self.recorder.write_metadata({"name": "retry_results"})
        video = Mock()
        video.has_pending_cleanup.return_value = False
        video.get_result.return_value = {"state": "completed", "error": None}
        self.recorder._video_recorders["camera"] = video
        self.recorder.stop()
        self.recorder.wait_stopped()
        original = (self.parent / "retry_results" / "metadata.json").read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.recorder.write_metadata({"recording_stopped_at": "test-stop-time"})
        self.assertEqual(original, (self.parent / "retry_results" / "metadata.json").read_bytes())
        self.recorder.write_metadata({"recording_stopped_at": "test-stop-time"})
        saved = json.loads((self.parent / "retry_results" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual("completed", saved["video_results"]["camera"]["state"])

    def test_invalid_metadata_preserves_previous_json(self):
        self.recorder.start(str(self.parent), "invalid_metadata", targets=numeric_targets())
        self.recorder.write_metadata({"devices": {}})
        folder = self.parent / "invalid_metadata"
        original = (folder / "metadata.json").read_bytes()
        with self.assertRaises(TypeError):
            self.recorder.write_metadata({"unsupported": object()})
        self.assertEqual(original, (folder / "metadata.json").read_bytes())
        self.assertEqual({"devices": {}}, self.recorder.metadata)
        self.assertEqual([], list(folder.glob(".metadata-*.tmp")))

    def test_csv_close_failure_keeps_file_reference_until_retry(self):
        self.recorder.start(str(self.parent), "retry_csv", targets=numeric_targets("meter"))
        output = self.recorder._files["meter"]

        class RetryClose:
            failed = False

            @property
            def closed(self):
                return output.closed

            def close(self):
                if not self.failed:
                    self.failed = True
                    raise OSError("CSV close failed")
                output.close()

        pending = RetryClose()
        self.recorder._files["meter"] = pending
        self.recorder.stop()
        with self.assertRaisesRegex(OSError, "CSV close failed"):
            self.recorder.wait_stopped()
        self.assertTrue(self.recorder.has_pending_cleanup())
        self.assertIs(pending, self.recorder._files["meter"])
        self.assertFalse(output.closed)
        self.recorder.stop()
        self.recorder.wait_stopped()
        self.assertTrue(output.closed)
        self.assertFalse(self.recorder.has_pending_cleanup())

    def test_metadata_replace_failure_preserves_previous_json_and_cleans_temporary_file(self):
        self.recorder.start(str(self.parent), "replace_metadata", targets=numeric_targets())
        self.recorder.write_metadata({"devices": {}})
        folder = self.parent / "replace_metadata"
        original = (folder / "metadata.json").read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self.recorder.write_metadata({"devices": {"pm_1": []}})
        self.assertEqual(original, (folder / "metadata.json").read_bytes())
        self.assertEqual({"devices": {}}, self.recorder.metadata)
        self.assertEqual([], list(folder.glob(".metadata-*.tmp")))

    def test_recording_api_returns_actual_state_across_two_recordings(self):
        app = create_app()
        with TestClient(app) as client:
            devices = [
                device for device in client.post("/api/devices/scan").json()
                if device["type"] == "power_meter"
            ]
            mm = app.state.measurement_manager
            # 数値CSVの記録対象となるパワーメーターの接続と取得を開始する。
            for device in devices:
                client.post(f"/api/devices/{device['id']}/connect").raise_for_status()
                mm.start_sampling(device["id"])

            for name in ("run_01", "run_02"):
                response = client.post("/api/recordings/start", json={"directory": str(self.parent), "name": name})
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(str(self.parent / name), response.json()["directory"])
                self.assertEqual(name, response.json()["name"])
                self.assertTrue(response.json()["is_recording"])
                metadata_path = self.parent / name / "metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                self.assertEqual(name, metadata["name"])
                self.assertEqual({device["id"] for device in devices}, set(metadata["devices"]))
                self.assertIsNone(metadata["recording_stopped_at"])

                # 設定APIからの変更通知を処理し、設定値と通知時刻がJSONに保存されることを確認する。
                device_id = devices[0]["id"]
                settings_path = f"/api/devices/{device_id}/settings"
                settings = client.get(settings_path).json()
                settings["wavelength"] += 50.0
                response = client.put(settings_path, json=settings)
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(settings, response.json())
                mm.data_queue.join()
                history = json.loads(metadata_path.read_text(encoding="utf-8"))["devices"][device_id]
                self.assertEqual(2, len(history))
                self.assertEqual(settings, history[-1]["settings"])
                self.assertEqual(mm.latest_settings[device_id].timestamp_ns, history[-1]["timestamp_ns"])

                # 不正な設定を送信し、記録状態と保存済みの履歴が変わらないことを確認する。
                saved_contents = metadata_path.read_bytes()
                response = client.put(settings_path, json={**settings, "averaging": "invalid"})
                self.assertEqual(422, response.status_code, response.text)
                mm.data_queue.join()
                self.assertTrue(mm.is_recording())
                self.assertEqual(saved_contents, metadata_path.read_bytes())

                deadline = time.monotonic() + 2
                while not all(writer.fieldnames for writer in mm._data_recorder._csv_writers.values()) and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(len(devices), len(mm._data_recorder._csv_writers))
                self.assertTrue(all(writer.fieldnames for writer in mm._data_recorder._csv_writers.values()))

                response = client.get("/api/recordings/status")
                self.assertEqual(200, response.status_code, response.text)
                self.assertTrue(response.json()["is_recording"])
                response = client.post("/api/recordings/stop")
                self.assertEqual(200, response.status_code, response.text)
                self.assertFalse(response.json()["is_recording"])
                self.assertFalse(mm.is_recording())
                self.assertIsNotNone(json.loads(metadata_path.read_text(encoding="utf-8"))["recording_stopped_at"])

                for device in devices:
                    rows = self.read_rows(self.parent / name / f"{device['id']}.csv")
                    self.assertGreater(len(rows), 0)
                    self.assertTrue(all(row["id"] == device["id"] for row in rows))
        self.assertIsNone(mm._processor_thread)


if __name__ == "__main__":
    unittest.main()
