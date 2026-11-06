"""DataRecorderが管理する終了待機、完了通知、失敗時の後処理を検証する。"""

import csv
import json
import logging
import queue
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.recordings import router
from backend.app.models.devices import CameraSetting, DeviceInfo
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.services.data_racorder import VideoRecorder
from backend.app.services.measurement_manager import MeasurementManager
from backend.app.utils.time_utils import get_counter_ns, get_datetime


class ControlledVideoRecorder(VideoRecorder):
    """実エンコードを使わず、画像処理と終了を別々のゲートで遅延させる。"""

    def __init__(self, *args):
        super().__init__(*args)
        self.frame_gate = threading.Event()
        self.finish_gate = threading.Event()
        self.frame_gate.set()
        self.finish_gate.set()
        self.processing = threading.Event()
        self.frames = []

    def start(self):
        self._queue = queue.Queue(maxsize=self.QUEUE_MAX_FRAMES)
        self._accepting = True
        self._state = "recording"
        self._ffmpeg_process = Mock()
        self._ffmpeg_process.poll.return_value = 0
        self._thread = threading.Thread(target=self._run)
        self._thread.start()

    def _run(self):
        try:
            while not self._stop_event.is_set() or not self._queue.empty():
                try:
                    pixels, index, timestamp, device_timestamp, elapsed = self._queue.get(timeout=0.01)
                except queue.Empty:
                    continue
                try:
                    self.processing.set()
                    if not self.frame_gate.wait(5):
                        raise TimeoutError("Test did not release frame processing.")
                    self.frames.append(index)
                    self.written_frames += 1
                finally:
                    self._queue.task_done()
            if not self.finish_gate.wait(5):
                raise TimeoutError("Test did not release video finalization.")
        except Exception as exc:
            self._fail(exc)
        finally:
            with self._lock:
                self._accepting = False
                self._state = "failed" if self.error else ("completed" if self.frames else "empty")


class RecordingLifecycleTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="recording-lifecycle-")))
        self.enterContext(patch("backend.app.services.data_racorder.VideoRecorder", ControlledVideoRecorder))
        settings = CameraSetting(width=8, height=6, fps=10, exposure=0.05, gain=1)
        self.devices = {}
        self.sampling_queues = {}
        for device_id, kind in (("camera1", "camera"), ("camera2", "camera"), ("meter", "power_meter")):
            device = Mock()
            device.is_sampling.return_value = False
            device.get_info.return_value = DeviceInfo(
                id=device_id, type=kind, manufacturer="test", model="synthetic", serial_number=device_id,
                address=device_id, status="connected",
            )
            payload = settings.model_dump() if kind == "camera" else {"wavelength": 550.0}

            def start_sampling(data_queue, device=device, device_id=device_id, payload=payload):
                device.is_sampling.return_value = True
                self.sampling_queues[device_id] = data_queue
                data_queue.put(DeviceData(device_id, "settings", get_counter_ns(), payload))

            device.start_sampling.side_effect = start_sampling
            device.stop_sampling.side_effect = lambda device=device: setattr(device.is_sampling, "return_value", False)
            self.devices[device_id] = device
        manager = Mock()
        manager.get_all.return_value = self.devices
        manager.get.side_effect = self.devices.__getitem__
        self.events = Mock()
        self.events.is_running.return_value = True
        self.mm = MeasurementManager(manager, self.events)
        self.videos = []
        self.workers = []
        self.addCleanup(self.shutdown)
        for device_id in self.devices:
            self.mm.start_sampling(device_id)
        app = FastAPI()
        app.state.measurement_manager = self.mm
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def shutdown(self):
        for video in self.videos:
            if isinstance(video, ControlledVideoRecorder):
                video.frame_gate.set()
                video.finish_gate.set()
        for worker in self.workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        try:
            self.mm.shutdown()
        except RuntimeError:
            self.assertFalse(self.mm._data_recorder.is_stopping())
        for video in self.videos:
            self.assertFalse(video.is_running())
        self.assertIsNone(self.mm._processor_thread)

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(condition())

    def start(self, name="run"):
        response = self.client.post("/api/recordings/start", json={
            "directory": str(self.directory), "name": name,
            "video_options": {"camera1": {"format": "ffv1"}, "camera2": {"format": "ffv1"}},
        })
        self.assertEqual(200, response.status_code, response.text)
        videos = dict(self.mm._data_recorder._video_recorders)
        self.videos.extend(videos.values())
        return videos

    def frame(self, device_id="camera1", index=0):
        image = memoryview(bytes(range(48))).cast("B", shape=(6, 8))
        self.sampling_queues[device_id].put(DeviceData(device_id, "frame", get_counter_ns(), CameraFrame(image, index)))

    def metadata(self, name="run"):
        return json.loads((self.directory / name / "metadata.json").read_text(encoding="utf-8"))

    def test_stop_is_owned_by_data_recorder_and_does_not_block_processor(self):
        videos = self.start()
        videos["camera1"].finish_gate.clear()
        for index in range(3):
            self.frame(index=index)
        self.sampling_queues["meter"].put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 1.0}))
        responses = []
        worker = threading.Thread(target=lambda: responses.append(self.client.post("/api/recordings/stop")))
        self.workers.append(worker)
        worker.start()
        recorder = self.mm._data_recorder
        self.wait_until(recorder.is_stopping)
        self.assertFalse(hasattr(self.mm, "_finish_thread"))
        self.assertTrue(recorder._finish_thread.is_alive())
        self.assertFalse(recorder._stop_done.is_set())
        self.assertFalse(self.mm.is_recording())
        self.assertIsNone(self.mm.get_metadata().recording_stopped_at)

        published = threading.Event()
        self.events.publish.side_effect = lambda event, *, key: published.set()
        self.sampling_queues["meter"].put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 2.0}))
        self.frame(index=99)
        self.assertTrue(published.wait(1))
        self.assertTrue(worker.is_alive())
        denied = self.client.post("/api/recordings/start", json={"directory": str(self.directory), "name": "early"})
        self.assertEqual(503, denied.status_code)

        before_release = get_datetime()
        videos["camera1"].finish_gate.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(200, responses[0].status_code, responses[0].text)
        self.assertFalse(responses[0].json()["is_stopping"])
        self.assertTrue(recorder._stop_done.is_set())
        self.assertEqual([0, 1, 2], videos["camera1"].frames)
        saved = self.metadata()
        self.assertGreaterEqual(datetime.fromisoformat(saved["recording_stopped_at"]), before_release)
        self.assertEqual(self.mm.get_metadata().recording_stopped_at.isoformat(), saved["recording_stopped_at"])
        with (self.directory / "run" / "meter.csv").open(encoding="utf-8-sig", newline="") as file:
            self.assertEqual(["1.0"], [row["power_w"] for row in csv.DictReader(file)])

    def test_every_camera_is_requested_before_wait_and_duplicate_stop_reuses_worker(self):
        videos = self.start()
        for video in videos.values():
            video.finish_gate.clear()
        recorder = self.mm._data_recorder
        recorder.stop()
        thread = recorder._finish_thread
        self.assertTrue(all(video._stop_event.is_set() for video in videos.values()))
        recorder.stop()
        self.assertIs(thread, recorder._finish_thread)
        for video in videos.values():
            video.finish_gate.set()
        recorder.wait_stopped()
        self.assertTrue(all(not video.is_running() for video in videos.values()))
        self.assertFalse(recorder.is_stopping())

    def test_api_waits_for_final_metadata_before_reply(self):
        self.start()
        entered = threading.Event()
        release = threading.Event()
        replace = Path.replace
        responses = []

        def delayed_replace(path, target):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release metadata saving.")
            return replace(path, target)

        with patch.object(Path, "replace", delayed_replace):
            worker = threading.Thread(target=lambda: responses.append(self.client.post("/api/recordings/stop")))
            self.workers.append(worker)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(worker.is_alive())
                self.assertFalse(self.mm._data_recorder._stop_done.is_set())
            finally:
                release.set()
                worker.join(3)
        self.assertEqual(200, responses[0].status_code)
        self.assertIsNotNone(self.metadata()["recording_stopped_at"])

    def test_timeout_retains_live_video_and_retry_can_finish(self):
        videos = self.start()
        video = videos["camera1"]
        video.STOP_TIMEOUT = 0.02
        video.finish_gate.clear()
        response = self.client.post("/api/recordings/stop")
        self.assertEqual(503, response.status_code)
        recorder = self.mm._data_recorder
        self.assertTrue(recorder.is_stopping())
        self.assertTrue(recorder._stop_done.is_set())
        self.assertIs(video, recorder._video_recorders["camera1"])
        self.assertIsNone(self.mm.get_metadata().recording_stopped_at)
        self.assertIsNone(self.metadata()["recording_stopped_at"])
        denied = self.client.post("/api/recordings/start", json={"directory": str(self.directory), "name": "early"})
        self.assertEqual(503, denied.status_code)

        video.finish_gate.set()
        self.wait_until(lambda: not video.is_running())
        self.assertEqual(503, self.client.post("/api/recordings/stop").status_code)
        self.assertFalse(recorder.is_stopping())
        self.assertIsNotNone(self.metadata()["recording_stopped_at"])
        self.start("next")
        self.assertIsNone(recorder.error)
        self.assertNotIn("video_results", self.metadata("next"))
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)

    def test_new_recording_has_an_independent_completion_event(self):
        self.start("first")
        recorder = self.mm._data_recorder
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)
        old_event = recorder._stop_done
        original = (self.directory / "first" / "metadata.json").read_bytes()
        self.start("second")
        new_event = recorder._stop_done
        self.assertIsNot(old_event, new_event)
        old_event.set()
        self.assertFalse(new_event.is_set())
        self.assertTrue(recorder.is_recording())
        self.assertEqual(original, (self.directory / "first" / "metadata.json").read_bytes())
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)

    def test_partial_start_failure_cleans_up_without_blocking_processor(self):
        self.start("previous")
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)
        previous = (self.directory / "previous" / "metadata.json").read_bytes()
        start = ControlledVideoRecorder.start

        def start_or_fail(video):
            self.videos.append(video)
            if video.path.stem == "camera2":
                raise RuntimeError("Second camera could not start.")
            video.finish_gate.clear()
            start(video)

        responses = []
        with patch.object(ControlledVideoRecorder, "start", start_or_fail):
            worker = threading.Thread(target=lambda: responses.append(self.client.post("/api/recordings/start", json={
                "directory": str(self.directory), "name": "failed",
                "video_options": {"camera1": {"format": "ffv1"}, "camera2": {"format": "ffv1"}},
            })))
            self.workers.append(worker)
            worker.start()
            self.wait_until(self.mm._data_recorder.is_stopping)
            self.assertTrue(worker.is_alive())
            published = threading.Event()
            self.events.publish.side_effect = lambda event, *, key: published.set()
            self.sampling_queues["meter"].put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 1.0}))
            self.assertTrue(published.wait(1))
            for video in self.videos:
                video.finish_gate.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(503, responses[0].status_code)
        self.assertIn("Second camera", responses[0].json()["detail"])
        self.assertTrue(all(not video.is_running() for video in self.videos))
        self.assertEqual(previous, (self.directory / "previous" / "metadata.json").read_bytes())
        self.start("next")
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)

    def test_final_metadata_failure_returns_error_and_preserves_results_for_retry(self):
        videos = self.start()
        original = (self.directory / "run" / "metadata.json").read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("metadata unavailable")):
            response = self.client.post("/api/recordings/stop")
        self.assertEqual(503, response.status_code)
        self.assertIn("metadata unavailable", response.json()["detail"])
        self.assertFalse(self.mm._data_recorder.is_stopping())
        self.assertTrue(all(not video.is_running() for video in videos.values()))
        self.assertEqual(original, (self.directory / "run" / "metadata.json").read_bytes())
        self.assertEqual([], list((self.directory / "run").glob(".metadata-*.tmp")))
        stopped_at = self.mm.get_metadata().recording_stopped_at
        self.assertIsNotNone(stopped_at)
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)
        saved = self.metadata()
        self.assertEqual(stopped_at.isoformat(), saved["recording_stopped_at"])
        self.assertEqual({"camera1", "camera2"}, set(saved["video_results"]))
        self.mm._data_recorder.write_metadata({"recording_stopped_at": None})
        self.assertEqual(saved["video_results"], self.metadata()["video_results"])
        self.assertEqual(stopped_at.isoformat(), self.metadata()["recording_stopped_at"])

    def test_failure_to_start_finalizer_keeps_resources_for_retry(self):
        videos = self.start()
        start_thread = threading.Thread.start

        def fail_finalizer(thread):
            if thread.name == "recording-finalizer":
                raise RuntimeError("Finalizer could not start.")
            return start_thread(thread)

        with patch.object(threading.Thread, "start", fail_finalizer):
            response = self.client.post("/api/recordings/stop")
        recorder = self.mm._data_recorder
        self.assertEqual(503, response.status_code)
        self.assertIn("Finalizer", response.json()["detail"])
        self.assertTrue(recorder.is_stopping())
        self.assertTrue(recorder._stop_done.is_set())
        self.assertEqual(set(videos), set(recorder._video_recorders))
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)
        self.assertFalse(recorder.is_stopping())

    def test_shutdown_waits_for_recording_before_stopping_processor(self):
        videos = self.start()
        videos["camera1"].finish_gate.clear()
        errors = []

        def shutdown():
            try:
                self.mm.shutdown()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=shutdown)
        self.workers.append(worker)
        worker.start()
        self.wait_until(self.mm._data_recorder.is_stopping)
        self.assertTrue(self.mm._processor_thread.is_alive())
        videos["camera1"].finish_gate.set()
        worker.join(3)
        self.assertEqual([], errors)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.mm._processor_thread)
        self.assertIsNotNone(self.metadata()["recording_stopped_at"])

    def test_shutdown_timeout_preserves_processor_for_retry(self):
        videos = self.start()
        video = videos["camera1"]
        video.STOP_TIMEOUT = 0.02
        video.finish_gate.clear()
        with self.assertRaises(TimeoutError):
            self.mm.shutdown()
        self.assertTrue(self.mm._data_recorder.is_stopping())
        self.assertTrue(self.mm._processor_thread.is_alive())
        video.finish_gate.set()
        self.wait_until(lambda: not video.is_running())
        with self.assertRaises(RuntimeError):
            self.mm.shutdown()
        self.assertFalse(self.mm._data_recorder.is_stopping())
        self.assertIsNone(self.mm._processor_thread)

    def test_shutdown_retains_processor_until_final_metadata_retry_succeeds(self):
        self.start()
        with patch.object(Path, "replace", side_effect=OSError("final metadata unavailable")):
            with self.assertRaisesRegex(OSError, "final metadata unavailable"):
                self.mm.shutdown()
        recorder = self.mm._data_recorder
        self.assertFalse(recorder.is_stopping())
        self.assertTrue(recorder.has_pending_cleanup())
        self.assertTrue(self.mm._processor_thread.is_alive())
        stopped_at = self.mm.get_metadata().recording_stopped_at
        with self.assertRaisesRegex(RuntimeError, "cleanup has not finished"):
            recorder.start(str(self.directory), "denied", targets={})
        for action in (
            lambda: self.mm.start_sampling("meter"),
            lambda: self.mm.start_recording(str(self.directory), "denied"),
            lambda: self.mm.set_settings("camera1", {}),
        ):
            with self.assertRaisesRegex(RuntimeError, "shutting down"):
                action()
        self.mm.shutdown()
        self.assertFalse(recorder.has_pending_cleanup())
        self.assertIsNone(self.mm._processor_thread)
        self.assertEqual(stopped_at.isoformat(), self.metadata()["recording_stopped_at"])

    def test_shutdown_sampling_failure_stops_other_devices_and_keeps_recording(self):
        self.start()
        with patch.object(self.devices["camera1"], "stop_sampling", side_effect=RuntimeError("sampling failure")):
            with self.assertRaisesRegex(RuntimeError, "Could not stop all"):
                self.mm.shutdown()
        self.assertTrue(self.devices["camera1"].is_sampling())
        self.assertFalse(self.devices["camera2"].is_sampling())
        self.assertFalse(self.devices["meter"].is_sampling())
        self.assertTrue(self.mm.is_recording())
        self.assertTrue(self.mm._processor_thread.is_alive())
        self.mm.shutdown()

    def test_monitor_failure_does_not_skip_sampling_stop(self):
        self.start()
        with patch.object(self.mm, "_stop_queue_monitor", side_effect=TimeoutError("monitor failure")):
            with self.assertRaisesRegex(TimeoutError, "monitor failure"):
                self.mm.shutdown()
        self.assertFalse(any(device.is_sampling() for device in self.devices.values()))
        self.assertTrue(self.mm._processor_thread.is_alive())
        self.assertTrue(self.mm.is_recording())
        self.mm.shutdown()

    def test_stopped_processor_with_pending_data_returns_failure_without_join(self):
        self.mm.stop_all_sampling()
        self.mm.data_queue.join()
        self.mm._stop_processor()
        self.sampling_queues["meter"].put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 1.0}))
        try:
            with self.assertRaisesRegex(RuntimeError, "1 unprocessed queue items"):
                self.mm.shutdown()
            self.assertEqual(1, self.mm.data_queue.qsize())
            self.assertIsNotNone(self.mm._start_counter_ns)
        finally:
            self.mm.data_queue.get_nowait()
            self.mm.data_queue.task_done()

    def test_metadata_error_before_stop_command_still_notifies_command_completion(self):
        videos = self.start()
        videos["camera1"].finish_gate.clear()
        responses = []
        original = self.mm._save_queue_error_metadata
        failed = False
        commands = []
        real_put = self.mm._put_processor_command

        def put(command, **kwargs):
            commands.append(command)
            real_put(command, **kwargs)

        def save():
            nonlocal failed
            if not failed:
                failed = True
                raise OSError("queue metadata failure")
            original()

        with patch.object(self.mm, "_save_queue_error_metadata", side_effect=save), \
                patch.object(self.mm, "_put_processor_command", side_effect=put):
            worker = threading.Thread(target=lambda: responses.append(self.client.post("/api/recordings/stop")))
            self.workers.append(worker)
            worker.start()
            self.wait_until(lambda: bool(commands))
            self.assertTrue(commands[0].done.wait(1))
            videos["camera1"].finish_gate.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(503, responses[0].status_code)
        self.assertIn("queue metadata failure", responses[0].json()["detail"])

    def test_shutdown_command_wait_timeout_preserves_processor_and_can_retry(self):
        self.start()
        entered, release = threading.Event(), threading.Event()
        real_elapsed = self.mm._get_elapsed

        def elapsed(timestamp, period):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release sample processing.")
            return real_elapsed(timestamp, period)

        try:
            with patch.object(self.mm, "_get_elapsed", side_effect=elapsed):
                self.sampling_queues["meter"].put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 1.0}))
                self.assertTrue(entered.wait(1))
                self.mm.SHUTDOWN_WAIT_TIMEOUT = 0.03
                with self.assertRaisesRegex(TimeoutError, "recording command"):
                    self.mm.shutdown()
                self.assertTrue(self.mm._processor_thread.is_alive())
                release.set()
                self.wait_until(lambda: not self.mm._data_recorder.has_pending_cleanup())
            self.mm.shutdown()
            self.assertIsNone(self.mm._processor_thread)
        finally:
            release.set()

    def test_shutdown_timeout_applies_while_final_metadata_is_writing(self):
        self.start()
        entered, release = threading.Event(), threading.Event()
        replace = Path.replace

        def delayed_replace(path, target):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release final metadata writing.")
            return replace(path, target)

        try:
            with patch.object(Path, "replace", delayed_replace):
                self.mm._request_recording_stop()
                self.assertTrue(entered.wait(1))
                with self.assertRaisesRegex(TimeoutError, "recording finalization"):
                    self.mm._data_recorder.wait_stopped(timeout=0.02)
                self.mm.SHUTDOWN_WAIT_TIMEOUT = 0.03
                with self.assertRaisesRegex(TimeoutError, "recording finalization"):
                    self.mm.shutdown()
                self.assertTrue(self.mm._processor_thread.is_alive())
                self.assertTrue(self.mm._data_recorder.has_pending_cleanup())
                release.set()
            self.wait_until(lambda: not self.mm._data_recorder.has_pending_cleanup())
            self.mm.shutdown()
        finally:
            release.set()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_real_ffmpeg_failure_releases_waiter_and_finishes_other_devices(self):
        with patch("backend.app.services.data_racorder.VideoRecorder", VideoRecorder):
            videos = self.start()
        video = videos["camera1"]
        video._ffmpeg_process.kill()
        video._ffmpeg_process.wait(timeout=3)
        self.wait_until(lambda: video.error is not None)
        self.assertTrue(self.mm.is_recording())
        self.frame("camera2", 0)
        self.sampling_queues["meter"].put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 1.0}))
        self.assertEqual(503, self.client.post("/api/recordings/stop").status_code)
        saved = self.metadata()
        self.assertEqual("failed", saved["video_results"]["camera1"]["state"])
        self.assertEqual("completed", saved["video_results"]["camera2"]["state"])
        with (self.directory / "run" / "meter.csv").open(encoding="utf-8-sig", newline="") as file:
            self.assertEqual(1, len(list(csv.DictReader(file))))
