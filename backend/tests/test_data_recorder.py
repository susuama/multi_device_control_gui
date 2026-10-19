"""記録フォルダ・CSV・記録APIの回帰テスト。出力は一時フォルダに限定する。"""

import csv
import json
import logging
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.app.models.measurement import DeviceData
from backend.app.services.data_racorder import DataRecorder


class DataRecorderTests(unittest.TestCase):
    def setUp(self):
        previous_logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous_logging_disable)
        self.parent = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="data-recorder-tests-")))
        self.recorder = DataRecorder()
        # CSVを閉じてから一時フォルダを削除する。
        self.addCleanup(self.recorder.stop)

    def read_rows(self, path):
        with path.open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))

    def test_start_creates_recording_folder_and_serializable_status(self):
        self.assertIsNone(self.recorder.get_status().directory)
        self.recorder.start(str(self.parent), "run_01")

        status = self.recorder.get_status()
        self.assertTrue((self.parent / "run_01").is_dir())
        self.assertTrue(status.is_recording)
        self.assertEqual(str(self.parent / "run_01"), status.directory)
        self.assertEqual("run_01", status.name)
        self.assertEqual(status.model_dump(), status.model_dump(mode="json"))

    def test_consecutive_recordings_keep_data_separate(self):
        for name, power in (("run_01", 0.1), ("run_02", 0.2)):
            self.recorder.start(str(self.parent), name)
            self.recorder.write(DeviceData("pm_1", "sample", 100, {"power_w": power}), elapsed_s=0.0)
            self.recorder.stop()

        self.assertFalse((self.parent / "pm_1.csv").exists())
        self.assertEqual("0.1", self.read_rows(self.parent / "run_01" / "pm_1.csv")[0]["power_w"])
        self.assertEqual("0.2", self.read_rows(self.parent / "run_02" / "pm_1.csv")[0]["power_w"])

    def test_existing_recording_folder_is_not_reused(self):
        self.recorder.start(str(self.parent), "run_01")
        self.recorder.write(DeviceData("pm_1", "sample", 100, {"power_w": 0.1}))
        self.recorder.stop()
        path = self.parent / "run_01" / "pm_1.csv"
        original = path.read_bytes()

        with self.assertRaises(FileExistsError):
            self.recorder.start(str(self.parent), "run_01")

        self.assertFalse(self.recorder.is_recording())
        self.assertTrue(self.recorder.error)
        self.assertEqual(original, path.read_bytes())

    def test_recording_name_cannot_escape_parent_folder(self):
        for name in ("..", "../outside", str(self.parent / "outside")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.recorder.start(str(self.parent), name)
        self.assertFalse(self.recorder.is_recording())
        self.assertEqual([], list(self.parent.iterdir()))

    def test_per_device_csv_appends_rows_and_preserves_zero_elapsed(self):
        self.recorder.start(str(self.parent), "run_01")
        self.recorder.write(DeviceData("pm_1", "sample", 100, {"power_w": 0.1}), elapsed_s=0.0)
        self.recorder.write(DeviceData("pm_1", "sample", 200, {"power_w": 0.2}), elapsed_s=0.1)
        self.recorder.write(DeviceData("pm_2", "sample", 150, {"power_w": 0.3}), elapsed_s=0.05)
        self.recorder.stop()

        first_path = self.parent / "run_01" / "pm_1.csv"
        rows = self.read_rows(first_path)
        self.assertTrue(first_path.read_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertEqual(2, len(rows))
        self.assertEqual(["pm_1", "pm_1"], [row["id"] for row in rows])
        self.assertEqual("0.0", rows[0]["elapsed_s"])
        self.assertEqual("pm_2", self.read_rows(self.parent / "run_01" / "pm_2.csv")[0]["id"])

    def test_filename_collisions_keep_all_devices_separate(self):
        ids = ["pm/a", "pm:a", "PM_A", "pm_a_2"]
        self.recorder.start(str(self.parent), "run_01")
        for device_id in ids:
            self.recorder.write(DeviceData(device_id, "sample", 100, {"power_w": 0.1}))
            self.recorder.write(DeviceData(device_id, "sample", 200, {"power_w": 0.2}))
        self.assertTrue(self.recorder.is_recording())
        self.recorder.stop()

        paths = list((self.parent / "run_01").glob("*.csv"))
        self.assertEqual(len(ids), len(paths))
        saved_ids = []
        for path in paths:
            rows = self.read_rows(path)
            self.assertEqual(2, len(rows))
            self.assertEqual(rows[0]["id"], rows[1]["id"])
            saved_ids.append(rows[0]["id"])
        self.assertCountEqual(ids, saved_ids)

    def test_existing_csv_is_preserved_and_write_failure_closes_files(self):
        self.recorder.start(str(self.parent), "run_01")
        path = self.parent / "run_01" / "pm_1.csv"
        path.write_bytes(b"existing measurement")
        self.recorder.write(DeviceData("pm_2", "sample", 100, {"power_w": 0.1}))
        opened = list(self.recorder._files.values())

        with self.assertRaises(FileExistsError):
            self.recorder.write(DeviceData("pm_1", "sample", 200, {"power_w": 0.2}))

        self.assertEqual(b"existing measurement", path.read_bytes())
        self.assertFalse(self.recorder.is_recording())
        self.assertTrue(self.recorder.error)
        self.assertTrue(all(file.closed for file in opened))

    def test_metadata_is_saved_and_can_be_finalized_after_stop(self):
        with self.assertRaisesRegex(RuntimeError, "Recording has not started"):
            self.recorder.write_metadata({})
        self.recorder.start(str(self.parent), "metadata")
        metadata = {"devices": {"pm_1": [{"settings": {"wavelength": 550.0}}]}}
        self.recorder.write_metadata(metadata)
        metadata["devices"]["pm_1"][0]["settings"]["wavelength"] = 650.0
        self.assertEqual(550.0, self.recorder.metadata["devices"]["pm_1"][0]["settings"]["wavelength"])

        self.recorder.stop()
        self.recorder.write_metadata({"recording_stopped_at": "2026-10-19T12:00:00+09:00"})
        path = self.parent / "metadata" / "metadata.json"
        self.assertEqual({"recording_stopped_at": "2026-10-19T12:00:00+09:00"}, json.loads(path.read_text(encoding="utf-8")))
        self.recorder.start(str(self.parent), "next_metadata")
        self.assertEqual({}, self.recorder.metadata)

    def test_invalid_metadata_preserves_previous_json(self):
        self.recorder.start(str(self.parent), "invalid_metadata")
        self.recorder.write_metadata({"devices": {}})
        folder = self.parent / "invalid_metadata"
        original = (folder / "metadata.json").read_bytes()
        with self.assertRaises(TypeError):
            self.recorder.write_metadata({"unsupported": object()})
        self.assertEqual(original, (folder / "metadata.json").read_bytes())
        self.assertEqual({"devices": {}}, self.recorder.metadata)
        self.assertEqual([], list(folder.glob(".metadata-*.tmp")))

    def test_metadata_replace_failure_preserves_previous_json_and_cleans_temporary_file(self):
        self.recorder.start(str(self.parent), "replace_metadata")
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
            devices = client.post("/api/devices/scan").json()
            mm = app.state.measurement_manager
            # 記録APIの前提として、ダミー2台の接続と取得を開始する。
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
                while len(mm._data_recorder._writers) < len(devices) and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertEqual(len(devices), len(mm._data_recorder._writers))

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
