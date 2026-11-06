"""記録ID・対象ごとの保存結果と、終了処理中の状態取得を検証する。"""

import csv
import json
import logging
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.devices import router as devices_router
from backend.app.api.recordings import router as recordings_router
from backend.app.models.devices import CameraSetting, DeviceInfo
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.models.recordings import RecordingTarget
from backend.app.services.data_racorder import DataRecorder
from backend.app.services.measurement_manager import MeasurementManager
from backend.app.utils.time_utils import get_counter_ns
from backend.tests.test_recording_lifecycle import ControlledVideoRecorder


class DataRecorderStatusTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="recording-status-")))
        self.recorder = DataRecorder(server_instance_id="server-test")
        self.workers = []
        self.releases = []
        self.addCleanup(self.finish)

    def finish(self):
        for release in self.releases:
            release.set()
        for worker in self.workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.recorder.stop()
        self.recorder.wait_stopped(3)

    def start(self, name="run", ids=("meter",), measurement_id="measurement-test"):
        targets = {key: RecordingTarget(type="power_meter", settings={"wavelength": 550.0}) for key in ids}
        self.recorder.start(str(self.directory), name, targets=targets, measurement_id=measurement_id)
        return targets

    def test_initial_state_and_ids_survive_stop_then_change_at_next_recording(self):
        idle = self.recorder.get_status()
        self.assertEqual("server-test", idle.server_instance_id)
        self.assertIsNone(idle.recording_id)
        self.assertIsNone(idle.measurement_id)
        self.assertEqual({}, idle.targets)
        self.start()
        started = self.recorder.get_status()
        self.assertTrue(started.recording_id)
        self.assertEqual("measurement-test", started.measurement_id)
        self.recorder.stop()
        self.recorder.wait_stopped(3)
        stopped = self.recorder.get_status()
        self.assertEqual(started.recording_id, stopped.recording_id)
        self.assertEqual(started.measurement_id, stopped.measurement_id)
        self.assertEqual("empty", stopped.targets["meter"].state)
        self.assertEqual({}, self.recorder._files)
        self.start("next", measurement_id="measurement-next")
        next_status = self.recorder.get_status()
        self.assertNotEqual(started.recording_id, next_status.recording_id)
        self.assertEqual("measurement-next", next_status.measurement_id)
        self.assertEqual("server-test", next_status.server_instance_id)

    def test_settings_and_status_are_independent_copies(self):
        target = RecordingTarget(type="power_meter", settings={"nested": {"value": 1}})
        self.recorder.start(str(self.directory), "copy", targets={"meter": target})
        target.settings["nested"]["value"] = 2
        status = self.recorder.get_status()
        self.assertEqual(1, status.targets["meter"].settings_at_start["nested"]["value"])
        status.targets["meter"].settings_at_start["nested"]["value"] = 3
        status.targets.clear()
        self.assertEqual(1, self.recorder.get_status().targets["meter"].settings_at_start["nested"]["value"])

    def test_completed_and_empty_are_based_on_successful_csv_rows(self):
        self.start(ids=("data", "empty"))
        self.recorder.write(DeviceData("data", "sample", 1, {"power_w": 1.0}))
        self.recorder.stop()
        self.recorder.wait_stopped(3)
        status = self.recorder.get_status()
        self.assertEqual("completed", status.targets["data"].state)
        self.assertEqual("empty", status.targets["empty"].state)
        self.assertIsNone(status.targets["data"].error)

    def test_validation_or_folder_failure_preserves_previous_context(self):
        self.start()
        self.recorder.stop()
        self.recorder.wait_stopped(3)
        previous = self.recorder.get_status()
        for name in ("../invalid", "run"):
            with self.subTest(name=name), self.assertRaises((ValueError, FileExistsError)):
                self.start(name, ids=("different",), measurement_id="different")
            current = self.recorder.get_status()
            self.assertEqual(previous.recording_id, current.recording_id)
            self.assertEqual(previous.measurement_id, current.measurement_id)
            self.assertEqual(previous.targets, current.targets)

    def test_partial_start_failure_retains_all_targets_with_distinct_states(self):
        original = csv.DictWriter
        calls = 0

        def writer(file, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("writer unavailable")
            return original(file, **kwargs)

        with patch("backend.app.services.data_racorder.csv.DictWriter", side_effect=writer):
            with self.assertRaisesRegex(OSError, "writer unavailable"):
                self.start(ids=("ready", "failed", "untouched"))
        with self.assertRaisesRegex(OSError, "writer unavailable"):
            self.recorder.wait_stopped(3)
        status = self.recorder.get_status()
        self.assertTrue(status.recording_id)
        self.assertEqual("measurement-test", status.measurement_id)
        self.assertEqual({"ready", "failed", "untouched"}, set(status.targets))
        self.assertEqual("empty", status.targets["ready"].state)
        self.assertEqual("failed", status.targets["failed"].state)
        self.assertEqual("writer unavailable", status.targets["failed"].error)
        self.assertEqual("not_started", status.targets["untouched"].state)
        self.assertIsNone(status.targets["untouched"].error)

    def test_write_failure_is_attributed_only_to_affected_target(self):
        self.start(ids=("failed", "other"))
        self.recorder.write(DeviceData("failed", "sample", 1, {"power_w": 1.0}))
        with self.assertRaises(ValueError):
            self.recorder.write(DeviceData("failed", "sample", 2, {"power_w": 2.0, "extra": 1}))
        with self.assertRaises(ValueError):
            self.recorder.wait_stopped(3)
        status = self.recorder.get_status()
        self.assertEqual("failed", status.targets["failed"].state)
        self.assertIn("extra", status.targets["failed"].error)
        self.assertEqual("empty", status.targets["other"].state)
        self.assertIsNone(status.targets["other"].error)

    def test_close_failure_keeps_failed_target_and_context_after_cleanup_retry(self):
        self.start()
        output = self.recorder._files["meter"]
        pending = Mock()
        type(pending).closed = property(lambda _: output.closed)
        pending.close.side_effect = [OSError("close unavailable"), None]
        self.recorder._files["meter"] = pending
        self.recorder.stop()
        with self.assertRaisesRegex(OSError, "close unavailable"):
            self.recorder.wait_stopped(3)
        failed = self.recorder.get_status()
        self.assertTrue(failed.is_stopping)
        self.assertEqual("failed", failed.targets["meter"].state)
        pending.close.side_effect = output.close
        self.recorder.stop()
        self.recorder.wait_stopped(3)
        recovered = self.recorder.get_status()
        self.assertFalse(self.recorder.has_pending_cleanup())
        self.assertEqual(failed.recording_id, recovered.recording_id)
        self.assertEqual(failed.targets, recovered.targets)

    def test_metadata_failure_does_not_mark_successful_targets_failed(self):
        self.start()
        self.recorder.write(DeviceData("meter", "sample", 1, {"power_w": 1.0}))
        with patch.object(Path, "replace", side_effect=OSError("metadata unavailable")):
            self.recorder.stop()
            with self.assertRaisesRegex(OSError, "metadata unavailable"):
                self.recorder.wait_stopped(3)
        failed = self.recorder.get_status()
        self.assertEqual("completed", failed.targets["meter"].state)
        self.assertIsNone(failed.targets["meter"].error)
        self.assertTrue(self.recorder.has_pending_cleanup())
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            self.start("too-early")
        self.recorder.stop()
        self.recorder.wait_stopped(3)
        self.assertEqual(failed.recording_id, self.recorder.get_status().recording_id)
        self.assertFalse(self.recorder.has_pending_cleanup())

    def test_metadata_write_and_context_switch_are_serialized_without_blocking_status(self):
        self.start()
        self.recorder.stop()
        self.recorder.wait_stopped(3)
        previous_id = self.recorder.get_status().recording_id
        entered, release, started = threading.Event(), threading.Event(), threading.Event()
        self.releases.append(release)
        original = Path.replace

        def replace(path, target):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release JSON writing.")
            return original(path, target)

        def start_next():
            self.start("next")
            started.set()

        with patch.object(Path, "replace", replace):
            writer = threading.Thread(target=lambda: self.recorder.write_metadata({"old": True}))
            self.workers.append(writer)
            writer.start()
            self.assertTrue(entered.wait(1))
            starter = threading.Thread(target=start_next)
            self.workers.append(starter)
            starter.start()
            try:
                self.assertEqual(previous_id, self.recorder.get_status().recording_id)
                self.assertFalse(started.is_set())
                self.assertFalse((self.directory / "next").exists())
            finally:
                release.set()
                writer.join(3)
                starter.join(3)
        self.assertTrue(started.is_set())
        self.assertNotEqual(previous_id, self.recorder.get_status().recording_id)
        self.assertEqual({}, self.recorder.metadata)
        self.assertTrue(json.loads((self.directory / "run" / "metadata.json").read_text(encoding="utf-8"))["old"])


class RecordingStatusAPITests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="recording-status-api-")))
        self.enterContext(patch("backend.app.services.data_racorder.VideoRecorder", ControlledVideoRecorder))
        self.devices = {}
        self.sampling_queues = {}
        self.settings = {}
        for device_id, kind in (("camera", "camera"), ("empty-camera", "camera"), ("meter", "power_meter")):
            device = Mock()
            device.is_sampling.return_value = False
            device.get_info.return_value = DeviceInfo(
                id=device_id, type=kind, manufacturer="test", model="test", serial_number=device_id,
                address=device_id, status="connected",
            )
            settings = CameraSetting(width=8, height=6, fps=10, exposure=0.05, gain=1) if kind == "camera" else Mock()
            if kind != "camera":
                settings.model_dump.return_value = {"wavelength": 550.0, "averaging": 1}
            self.settings[device_id] = settings
            device.get_settings.return_value = settings
            device.set_settings.side_effect = lambda values: CameraSetting.model_validate(values)

            def start(data_queue, device=device, device_id=device_id, settings=settings):
                device.is_sampling.return_value = True
                self.sampling_queues[device_id] = data_queue
                data_queue.put(DeviceData(device_id, "settings", get_counter_ns(), settings.model_dump()))

            device.start_sampling.side_effect = start
            device.stop_sampling.side_effect = lambda device=device: setattr(device.is_sampling, "return_value", False)
            self.devices[device_id] = device
        manager = Mock()
        manager.get_all.return_value = self.devices
        manager.get.side_effect = self.devices.__getitem__
        events = Mock()
        events.is_running.return_value = False
        self.mm = MeasurementManager(manager, events, server_instance_id="api-server")
        self.videos = []
        self.workers = []
        self.releases = []
        self.addCleanup(self.finish)
        for device_id in self.devices:
            self.mm.start_sampling(device_id)
        app = FastAPI()
        app.state.measurement_manager = self.mm
        app.state.device_manager = manager
        app.include_router(recordings_router)
        app.include_router(devices_router)
        self.client = self.enterContext(TestClient(app))

    def finish(self):
        for release in self.releases:
            release.set()
        for video in self.videos:
            video.frame_gate.set()
            video.finish_gate.set()
        for worker in self.workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.mm.shutdown()

    def start(self, name="run"):
        response = self.client.post("/api/recordings/start", json={
            "directory": str(self.directory), "name": name,
            "video_options": {key: {"format": "ffv1"} for key in ("camera", "empty-camera")},
        })
        self.assertEqual(200, response.status_code, response.text)
        self.videos.extend(self.mm._data_recorder._video_recorders.values())
        return response.json()

    def status(self):
        response = self.client.get("/api/recordings/status")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def frame(self):
        image = memoryview(bytes(range(48))).cast("B", shape=(6, 8))
        self.sampling_queues["camera"].put(DeviceData("camera", "frame", get_counter_ns(), CameraFrame(image, 0)))

    def test_start_get_and_stop_share_ids_and_report_saved_or_empty_targets(self):
        idle = self.status()
        self.assertEqual("api-server", idle["server_instance_id"])
        self.assertIsNone(idle["recording_id"])
        self.assertEqual({}, idle["targets"])
        started = self.start()
        self.assertEqual(self.mm.get_measurement_status().measurement_id, started["measurement_id"])
        self.assertEqual(started, self.status())
        self.frame()
        self.sampling_queues["meter"].put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 1.0}))
        stopped_response = self.client.post("/api/recordings/stop")
        self.assertEqual(200, stopped_response.status_code, stopped_response.text)
        stopped = stopped_response.json()
        self.assertEqual(started["recording_id"], stopped["recording_id"])
        self.assertEqual(started["measurement_id"], stopped["measurement_id"])
        self.assertEqual("completed", stopped["targets"]["camera"]["state"])
        self.assertEqual("empty", stopped["targets"]["empty-camera"]["state"])
        self.assertEqual("completed", stopped["targets"]["meter"]["state"])
        self.assertEqual(stopped, self.status())
        self.assertTrue(all(target["locked_settings"] == [] for target in stopped["targets"].values()))

    def test_sampling_stop_or_addition_does_not_change_recording_targets(self):
        started = self.start()
        self.mm.stop_sampling("camera")
        other = Mock()
        other.is_sampling.return_value = True
        self.devices["later"] = other
        self.addCleanup(self.devices.pop, "later")
        current = self.status()
        self.assertEqual(started["targets"], current["targets"])
        self.assertEqual("recording", current["targets"]["camera"]["state"])
        self.assertEqual(started["recording_id"], current["recording_id"])

    def test_new_measurement_does_not_replace_previous_recordings_measurement_id(self):
        started = self.start()
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)
        self.mm.stop_all_sampling()
        self.mm.start_sampling("meter")
        self.assertNotEqual(started["measurement_id"], self.mm.get_measurement_status().measurement_id)
        self.assertEqual(started["measurement_id"], self.status()["measurement_id"])

    def test_start_command_keeps_measurement_id_captured_before_queue_submission(self):
        period = self.mm._measurement_period
        put = self.mm._put_processor_command

        def put_after_period_change(command):
            self.mm._measurement_period = replace(period, measurement_id="later-period")
            return put(command)

        try:
            with patch.object(self.mm, "_put_processor_command", side_effect=put_after_period_change):
                started = self.start()
            self.assertEqual(period.measurement_id, started["measurement_id"])
        finally:
            self.mm._measurement_period = period

    def test_queue_error_is_reported_globally_without_finishing_targets(self):
        started = self.start()
        self.mm._request_queue_stop("meter", queue_size=350)
        deadline = time.monotonic() + 1
        while not self.mm._queue_stop_attempted and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(self.mm._queue_stop_attempted)
        current = self.status()
        self.assertIn("queue is full", current["error"])
        self.assertTrue(current["is_recording"])
        self.assertEqual(started["targets"], current["targets"])

    def test_concurrent_metadata_write_cannot_erase_final_video_results(self):
        self.start()
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        recorder = self.mm._data_recorder
        original = Path.replace
        writes = 0

        def first_replace(path, target):
            nonlocal writes
            writes += 1
            if writes == 1:
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("Test did not release first JSON.")
            return original(path, target)

        with patch.object(Path, "replace", first_replace):
            writer = threading.Thread(target=lambda: recorder.write_metadata({"external": True}))
            self.workers.append(writer)
            writer.start()
            self.assertTrue(entered.wait(1))
            recorder.stop()
            try:
                for video in self.videos:
                    video._thread.join(1)
                    self.assertFalse(video.is_running())
                self.assertTrue(recorder.is_stopping())
            finally:
                release.set()
                writer.join(3)
            recorder.wait_stopped(3)
        saved = json.loads((self.directory / "run" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual({"camera", "empty-camera"}, set(saved["video_results"]))
        self.assertTrue(all(result["state"] == "empty" for result in saved["video_results"].values()))
        self.assertIsNotNone(saved["recording_stopped_at"])

    def test_locked_settings_match_api_rejection_even_after_individual_video_failure(self):
        started = self.start()
        self.assertEqual(["fps", "roi"], started["targets"]["camera"]["locked_settings"])
        self.assertEqual([], started["targets"]["meter"]["locked_settings"])
        video = self.mm._data_recorder._video_recorders["camera"]
        with video._lock:
            video._fail(RuntimeError("camera failed"))
        current = self.status()
        self.assertTrue(current["is_recording"])
        self.assertEqual("failed", current["targets"]["camera"]["state"])
        self.assertEqual("camera failed", current["targets"]["camera"]["error"])
        self.assertEqual("recording", current["targets"]["meter"]["state"])
        self.assertEqual(["fps", "roi"], current["targets"]["camera"]["locked_settings"])
        original = self.settings["camera"].model_dump(mode="json")
        path = "/api/devices/camera/settings"
        self.assertEqual(409, self.client.put(path, json=original | {"fps": 20}).status_code)
        self.assertEqual(200, self.client.put(path, json=original).status_code)
        self.assertEqual(200, self.client.put(path, json=original | {"gain": 2}).status_code)
        self.assertEqual(original, self.status()["targets"]["camera"]["settings_at_start"])
        self.assertEqual(503, self.client.post("/api/recordings/stop").status_code)
        self.assertEqual([], self.status()["targets"]["camera"]["locked_settings"])

    def test_get_returns_while_video_finalization_is_waiting(self):
        self.start()
        video = self.mm._data_recorder._video_recorders["camera"]
        video.finish_gate.clear()
        stopped, queried = threading.Event(), threading.Event()
        responses = []
        stopper = threading.Thread(target=lambda: (self.client.post("/api/recordings/stop"), stopped.set()))
        self.workers.append(stopper)
        stopper.start()
        deadline = time.monotonic() + 1
        while not self.mm._data_recorder.is_stopping() and time.monotonic() < deadline:
            time.sleep(0.005)
        getter = threading.Thread(target=lambda: (responses.append(self.status()), queried.set()))
        self.workers.append(getter)
        getter.start()
        try:
            self.assertTrue(queried.wait(1))
            self.assertFalse(stopped.is_set())
            self.assertTrue(responses[0]["is_stopping"])
            self.assertEqual("stopping", responses[0]["targets"]["camera"]["state"])
        finally:
            video.finish_gate.set()
            stopper.join(3)
            getter.join(3)

    def test_get_returns_while_final_json_is_waiting(self):
        self.start()
        entered, release, queried = threading.Event(), threading.Event(), threading.Event()
        self.releases.append(release)
        original = Path.replace
        responses = []

        def replace(path, target):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release final JSON.")
            return original(path, target)

        with patch.object(Path, "replace", replace):
            stopper = threading.Thread(target=lambda: self.client.post("/api/recordings/stop"))
            self.workers.append(stopper)
            stopper.start()
            self.assertTrue(entered.wait(1))
            getter = threading.Thread(target=lambda: (responses.append(self.status()), queried.set()))
            self.workers.append(getter)
            getter.start()
            try:
                self.assertTrue(queried.wait(1))
                self.assertTrue(stopper.is_alive())
                self.assertTrue(responses[0]["is_stopping"])
                self.assertEqual("empty", responses[0]["targets"]["camera"]["state"])
            finally:
                release.set()
                stopper.join(3)
                getter.join(3)


if __name__ == "__main__":
    unittest.main()
