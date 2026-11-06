"""カメラ保存の設定契約・振り分けと、利用可能なら実エンコードを検証する。"""

import csv
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.api.devices import router as devices_router
from backend.app.api.recordings import router
from backend.app.devices.camera.dummy_camera import DummyCamera
from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.models.devices import CameraSetting, DeviceInfo
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.models.recordings import RecordingTarget, StartRecordingRequest, VideoRecordingOptions
from backend.app.services.data_racorder import DataRecorder, VideoRecorder
from backend.app.services.device_manager import DeviceManager
from backend.app.services.measurement_manager import MeasurementManager
from backend.app.utils.time_utils import get_counter_ns


def camera_settings():
    """テスト用の小さなモノクロ画像と保存ROIの条件を返す。"""
    return CameraSetting(
        width=8, height=6, fps=10, exposure=0.05, gain=1,
        roi={"x": 2, "y": 1, "width": 4, "height": 3},
    )


def synthetic_frame(index=0):
    """外部画像ライブラリを使わず、8×6の画素配列を作成する。"""
    return CameraFrame(memoryview(bytes(range(48))).cast("B", shape=(6, 8)), index, 1000 + index)


class FrameSink(VideoRecorder):
    """受け渡しの確認だけに使用する、テスト専用の動画保存処理。"""

    def start(self):
        self.frames = []
        self.stopped = False
        self._state = "recording"

    def write(self, frame, timestamp_ns, elapsed_s):
        self.frames.append((frame, timestamp_ns, elapsed_s))

    def stop(self):
        self.stopped = True
        self._state = "completed" if self.frames else "empty"


class CameraSettingsAPITests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="camera-settings-")))
        self.enterContext(patch("backend.app.services.data_racorder.VideoRecorder", FrameSink))
        self.dm = DeviceManager([DummyCamera, DummyPowerMeter])
        events = Mock()
        events.is_running.return_value = False
        self.mm = MeasurementManager(self.dm, events)
        self.releases = []
        self.workers = []
        self.addCleanup(self.shutdown)
        self.dm.scan()
        self.camera_id = next(key for key, device in self.dm.get_all().items() if device.get_info().type == "camera")
        self.camera = self.dm.get(self.camera_id)
        self.camera.connect()
        self.mm.set_settings(self.camera_id, camera_settings())
        self.mm.start_sampling(self.camera_id)
        app = FastAPI()
        app.state.device_manager = self.dm
        app.state.measurement_manager = self.mm
        app.include_router(devices_router)
        self.client = self.enterContext(TestClient(app))
        self.path = f"/api/devices/{self.camera_id}/settings"

    def shutdown(self):
        for release in self.releases:
            release.set()
        for worker in self.workers:
            worker.join(3)
        try:
            self.mm.shutdown()
        finally:
            self.dm.shutdown()
        for worker in self.workers:
            self.assertFalse(worker.is_alive())

    def start_recording(self):
        self.mm.start_recording(
            str(self.directory), "settings",
            video_options={self.camera_id: VideoRecordingOptions(format="ffv1")},
        )

    def test_fps_and_roi_changes_are_rejected_before_apply_without_history_changes(self):
        self.start_recording()
        original = self.camera.get_settings().model_dump(mode="json")
        history = self.mm.get_metadata().devices[self.camera_id]
        with patch.object(self.camera, "_apply_settings", wraps=self.camera._apply_settings) as apply:
            for changes in ({"fps": 20}, {"roi": None}, {"roi": {"x": 0, "y": 0, "width": 4, "height": 3}}):
                with self.subTest(changes=changes):
                    response = self.client.put(self.path, json=original | changes)
                    self.assertEqual(409, response.status_code, response.text)
                    self.assertIn("FPS or ROI", response.json()["detail"])
            apply.assert_not_called()
        self.mm.data_queue.join()
        self.assertEqual(original, self.camera.get_settings().model_dump(mode="json"))
        self.assertEqual(history, self.mm.get_metadata().devices[self.camera_id])
        metadata = json.loads((self.directory / "settings" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(history[0]["settings"], metadata["devices"][self.camera_id][0]["settings"])
        self.assertEqual(len(history), len(metadata["devices"][self.camera_id]))

    def test_adding_roi_to_full_image_recording_is_rejected(self):
        settings = self.camera.get_settings().model_dump(mode="json") | {"roi": None}
        self.assertEqual(200, self.client.put(self.path, json=settings).status_code)
        self.start_recording()
        response = self.client.put(self.path, json=settings | {"roi": {"x": 0, "y": 0, "width": 4, "height": 3}})
        self.assertEqual(409, response.status_code, response.text)
        self.assertIsNone(self.camera.get_settings().roi)

    def test_same_values_and_exposure_gain_changes_preserve_recording_and_history(self):
        self.start_recording()
        original = self.camera.get_settings().model_dump(mode="json")
        response = self.client.put(self.path, json=original | {"fps": "10"})
        self.assertEqual(200, response.status_code, response.text)
        changed = original | {"exposure": 0.1, "gain": 2}
        response = self.client.put(self.path, json=changed)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(changed, response.json())
        self.mm.data_queue.join()
        history = self.mm.get_metadata().devices[self.camera_id]
        self.assertEqual(original, history[0]["settings"])
        self.assertEqual(changed, history[-1]["settings"])
        video = self.mm._data_recorder._video_recorders[self.camera_id]
        self.assertEqual(original, video.settings.model_dump(mode="json"))
        self.assertTrue(self.mm.is_recording())

    def test_sampling_stop_keeps_restriction_until_recording_stops(self):
        self.start_recording()
        self.mm.stop_sampling(self.camera_id)
        changed = self.camera.get_settings().model_dump(mode="json") | {"fps": 20, "roi": None}
        self.assertEqual(409, self.client.put(self.path, json=changed).status_code)
        self.mm.stop_recording()
        response = self.client.put(self.path, json=changed)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(changed, response.json())

    def test_camera_outside_recording_targets_can_change_fps_and_roi(self):
        self.start_recording()
        other = DummyCamera("other-camera")
        self.dm.register(other.device_id, other)
        other.connect()
        self.mm.set_settings(other.device_id, camera_settings())
        self.mm.start_sampling(other.device_id)
        changed = other.get_settings().model_dump(mode="json") | {"fps": 20, "roi": None}
        response = self.client.put(f"/api/devices/{other.device_id}/settings", json=changed)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(changed, response.json())
        self.assertFalse(self.mm._data_recorder.is_recording_device(other.device_id))

    def test_invalid_camera_settings_still_return_422_without_applying(self):
        self.start_recording()
        original = self.camera.get_settings().model_dump(mode="json")
        with patch.object(self.camera, "_apply_settings", wraps=self.camera._apply_settings) as apply:
            for changes in ({"fps": 0}, {"roi": {"x": 8, "y": 0, "width": 1, "height": 1}}, {"unknown": 1}):
                with self.subTest(changes=changes):
                    response = self.client.put(self.path, json=original | changes)
                    self.assertEqual(422, response.status_code, response.text)
            apply.assert_not_called()
        self.assertEqual(original, self.camera.get_settings().model_dump(mode="json"))

    def test_recording_power_meter_can_still_update_settings(self):
        meter_id = next(key for key, device in self.dm.get_all().items() if device.get_info().type == "power_meter")
        self.dm.connect(meter_id)
        self.mm.start_sampling(meter_id)
        self.start_recording()
        changed = self.mm.get_settings(meter_id).model_dump(mode="json") | {"wavelength": 700}
        response = self.client.put(f"/api/devices/{meter_id}/settings", json=changed)
        self.assertEqual(200, response.status_code, response.text)
        self.mm.data_queue.join()
        self.assertEqual(changed, self.mm.get_metadata().devices[meter_id][-1]["settings"])

    def test_settings_change_waits_for_recording_start_then_is_rejected(self):
        entered, release, attempted, completed = (threading.Event() for _ in range(4))
        self.releases.append(release)
        original_start = self.mm._data_recorder.start
        errors, responses = [], []

        def delayed_start(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test recording start timed out")
            return original_start(*args, **kwargs)

        def record():
            try:
                self.start_recording()
            except Exception as exc:
                errors.append(exc)

        def update():
            try:
                attempted.set()
                settings = self.camera.get_settings().model_dump(mode="json") | {"fps": 20}
                responses.append(self.client.put(self.path, json=settings))
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        with patch.object(self.mm._data_recorder, "start", side_effect=delayed_start):
            for operation in (record, update):
                worker = threading.Thread(target=operation)
                self.workers.append(worker)
                worker.start()
                if operation is record:
                    self.assertTrue(entered.wait(1))
            self.assertTrue(attempted.wait(1))
            self.assertFalse(completed.wait(0.05))
            release.set()
            for worker in self.workers:
                worker.join(3)
        self.assertEqual([], errors)
        self.assertTrue(completed.is_set())
        self.assertEqual(409, responses[0].status_code, responses[0].text)
        self.assertEqual(10, self.camera.get_settings().fps)

    def test_recording_start_waits_for_settings_apply_and_uses_new_settings(self):
        entered, release, attempted, completed = (threading.Event() for _ in range(4))
        self.releases.append(release)
        original_apply = self.camera._apply_settings
        changed = self.camera.get_settings().model_dump(mode="json") | {"fps": 20}
        errors, responses = [], []

        def delayed_apply(settings):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("test settings apply timed out")
            return original_apply(settings)

        def update():
            try:
                responses.append(self.client.put(self.path, json=changed))
            except Exception as exc:
                errors.append(exc)

        def record():
            try:
                attempted.set()
                self.start_recording()
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        with patch.object(self.camera, "_apply_settings", side_effect=delayed_apply):
            for operation in (update, record):
                worker = threading.Thread(target=operation)
                self.workers.append(worker)
                worker.start()
                if operation is update:
                    self.assertTrue(entered.wait(1))
            self.assertTrue(attempted.wait(1))
            self.assertFalse(completed.wait(0.05))
            release.set()
            for worker in self.workers:
                worker.join(3)
        self.assertEqual([], errors)
        self.assertTrue(completed.is_set())
        self.assertEqual(200, responses[0].status_code, responses[0].text)
        video = self.mm._data_recorder._video_recorders[self.camera_id]
        self.assertEqual(20, video.settings.fps)
        self.assertEqual(changed, self.mm.get_metadata().devices[self.camera_id][0]["settings"])


class CameraModelTests(unittest.TestCase):
    def test_roi_is_optional_and_full_image_is_supported(self):
        settings = camera_settings().model_dump()
        settings.pop("roi")
        self.assertIsNone(CameraSetting(**settings).roi)
        settings["roi"] = {"x": 0, "y": 0, "width": 8, "height": 6}
        self.assertEqual(8, CameraSetting(**settings).roi.width)

    def test_invalid_dimensions_timing_pixel_format_and_roi_are_rejected(self):
        for changes in (
            {"width": 0}, {"height": -1}, {"fps": 0}, {"fps": float("nan")},
            {"exposure": -0.1}, {"gain": -1}, {"pixel_format": "rgba"}, {"pixel_format": "mono16"},
            {"roi": {"x": -1, "y": 0, "width": 1, "height": 1}},
            {"roi": {"x": 0, "y": 0, "width": 0, "height": 1}},
            {"roi": {"x": 7, "y": 0, "width": 2, "height": 1}},
            {"roi": {"x": 0, "y": 5, "width": 1, "height": 2}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                CameraSetting(**(camera_settings().model_dump() | changes))

    def test_only_three_video_formats_are_accepted(self):
        for format_name, extension in (("h264_lossless", ".mkv"), ("ffv1", ".mkv"), ("mpeg4", ".mp4")):
            with self.subTest(format=format_name):
                options = VideoRecordingOptions(format=format_name)
                self.assertEqual(extension, options.extension)
        with self.assertRaises(ValidationError):
            VideoRecordingOptions(format="h264")
        with self.assertRaises(ValidationError):
            VideoRecordingOptions(format="ffv1", quality=1)

    def test_monochrome_rgb_and_bgr_pixel_formats_are_accepted(self):
        for pixel_format in ("mono8", "rgb24", "bgr24"):
            with self.subTest(pixel_format=pixel_format):
                settings = CameraSetting(**(camera_settings().model_dump() | {"pixel_format": pixel_format}))
                self.assertEqual(pixel_format, settings.pixel_format)
        settings = camera_settings().model_dump()
        settings.pop("pixel_format")
        self.assertEqual("mono8", CameraSetting(**settings).pixel_format)

    def test_existing_numeric_recording_request_needs_no_video_options(self):
        request = StartRecordingRequest(directory="records")
        self.assertEqual({}, request.video_options)

    def test_video_recorder_copies_nested_settings(self):
        settings = camera_settings()
        options = VideoRecordingOptions(format="ffv1")
        recorder = VideoRecorder(Path("camera.mkv"), settings, options)
        settings.roi.x = 0
        settings.exposure = 0.1
        self.assertEqual(2, recorder.settings.roi.x)
        self.assertEqual(0.05, recorder.settings.exposure)
        self.assertIsNot(options, recorder.options)

    def test_get_result_returns_an_independent_dictionary(self):
        recorder = VideoRecorder(Path("camera.mkv"), camera_settings(), VideoRecordingOptions(format="ffv1"))
        result = recorder.get_result()
        self.assertEqual({"state": "not_started", "error": None}, result)
        result["state"] = "failed"
        result["error"] = "modified"
        self.assertEqual({"state": "not_started", "error": None}, recorder.get_result())


class CameraRecorderTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="camera-recording-")))
        self.recorder = DataRecorder()
        self.addCleanup(self.stop_recorder)
        self.targets = {
            "meter": RecordingTarget(type="power_meter", settings={}),
            "camera": RecordingTarget(
                type="camera", settings=camera_settings().model_dump(),
                video_options=VideoRecordingOptions(format="ffv1"),
            ),
        }

    def stop_recorder(self):
        self.recorder.stop()
        self.recorder.wait_stopped()

    def start(self, name="recording"):
        self.recorder.start(
            str(self.directory), name, targets=self.targets,
        )

    def test_start_creates_distinct_recorders_and_frame_keeps_timestamps(self):
        with patch("backend.app.services.data_racorder.VideoRecorder", FrameSink):
            self.start()
        video = self.recorder._video_recorders["camera"]
        self.assertEqual({"meter"}, set(self.recorder._csv_writers))
        self.assertEqual(self.directory / "recording" / "camera.mkv", video.path)
        self.targets["camera"].settings["roi"]["x"] = 0
        self.assertEqual(2, video.settings.roi.x)
        frame = synthetic_frame(7)
        self.recorder.write(DeviceData("camera", "frame", 9000, frame), elapsed_s=0.25)
        self.recorder.write(DeviceData("meter", "sample", 9100, {"power_w": 0.5}), elapsed_s=0.3)
        self.recorder.stop()
        self.recorder.wait_stopped()
        self.assertEqual([(frame, 9000, 0.25)], video.frames)
        self.assertTrue(video.stopped)
        self.assertFalse((self.directory / "recording" / "camera.csv").exists())
        with (self.directory / "recording" / "meter.csv").open(encoding="utf-8-sig", newline="") as file:
            self.assertEqual("0.5", next(csv.DictReader(file))["power_w"])
        self.assertEqual({}, self.recorder._video_recorders)

    def test_all_formats_reach_video_recorder(self):
        for format_name in ("h264_lossless", "ffv1", "mpeg4"):
            with self.subTest(format=format_name), patch("backend.app.services.data_racorder.VideoRecorder", FrameSink):
                self.targets["camera"] = self.targets["camera"].model_copy(
                    update={"video_options": VideoRecordingOptions(format=format_name)},
                )
                self.start(format_name)
                self.assertEqual(format_name, self.recorder._video_recorders["camera"].options.format)
                self.recorder.stop()
                self.recorder.wait_stopped()

    def test_invalid_target_options_and_camera_settings_fail_before_creating_folder(self):
        cases = (
            self.targets | {"camera": self.targets["camera"].model_copy(update={"video_options": None})},
            self.targets | {"meter": RecordingTarget(
                type="power_meter", settings={}, video_options=VideoRecordingOptions(format="ffv1"),
            )},
            self.targets | {"camera": RecordingTarget(
                type="camera", settings={}, video_options=VideoRecordingOptions(format="ffv1"),
            )},
        )
        for index, targets in enumerate(cases):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.recorder.start(
                    str(self.directory), str(index), targets=targets,
                )
            self.assertFalse((self.directory / str(index)).exists())
            self.assertFalse(self.recorder.is_recording())

    def test_missing_encoder_closes_already_created_csv_and_reports_failure(self):
        with patch("backend.app.services.data_racorder.shutil.which", return_value=None), self.assertRaisesRegex(FileNotFoundError, "FFmpeg"):
            self.start()
        with self.assertRaisesRegex(FileNotFoundError, "FFmpeg"):
            self.recorder.wait_stopped()
        self.assertFalse(self.recorder.is_recording())
        self.assertEqual({}, self.recorder._files)
        self.assertEqual({}, self.recorder._video_recorders)
        self.assertIn("FFmpeg", self.recorder.error)
        # Windowsでも追記用に開けることを確認する。
        with (self.directory / "recording" / "meter.csv").open("a"):
            pass

    def test_invalid_frame_payload_stops_recording(self):
        with patch("backend.app.services.data_racorder.VideoRecorder", FrameSink):
            self.start()
        video = self.recorder._video_recorders["camera"]
        with self.assertRaisesRegex(TypeError, "CameraFrame"):
            self.recorder.write(DeviceData("camera", "frame", 1, {}))
        with self.assertRaisesRegex(TypeError, "CameraFrame"):
            self.recorder.wait_stopped()
        self.assertTrue(video.stopped)
        self.assertFalse(self.recorder.is_recording())


class CameraRecordingAPITests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="camera-api-")))
        self.settings = camera_settings()
        self.camera = Mock()
        self.camera.is_sampling.return_value = False
        self.camera.get_info.return_value = DeviceInfo(
            id="camera", type="camera", manufacturer="test", model="synthetic", serial_number="1",
            address="test", status="connected",
        )
        manager = Mock()
        manager.get_all.return_value = {"camera": self.camera}
        manager.get.return_value = self.camera
        self.events = Mock()
        self.events.is_running.return_value = True
        self.mm = MeasurementManager(manager, self.events)
        self.addCleanup(self.mm.shutdown)

        def start_sampling(data_queue):
            self.camera.is_sampling.return_value = True
            self.sampling_queue = data_queue
            data_queue.put(DeviceData("camera", "settings", get_counter_ns(), self.settings.model_dump()))

        self.camera.start_sampling.side_effect = start_sampling
        self.camera.stop_sampling.side_effect = lambda: setattr(self.camera.is_sampling, "return_value", False)
        self.mm.start_sampling("camera")
        app = FastAPI()
        app.state.measurement_manager = self.mm
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def start_request(self, **changes):
        body = {
            "directory": str(self.directory), "name": "api",
            "video_options": {"camera": {"format": "ffv1"}},
        }
        return self.client.post("/api/recordings/start", json=body | changes)

    def test_api_config_frame_metadata_and_numeric_websocket_remain_separate(self):
        with patch("backend.app.services.data_racorder.VideoRecorder", FrameSink):
            response = self.start_request()
        self.assertEqual(200, response.status_code, response.text)
        video = self.mm._data_recorder._video_recorders["camera"]
        self.assertEqual("ffv1", video.options.format)
        frame = synthetic_frame()
        timestamp_ns = get_counter_ns()
        self.sampling_queue.put(DeviceData("camera", "frame", timestamp_ns, frame))
        self.sampling_queue.put(DeviceData("meter", "sample", timestamp_ns, {"power_w": 1.0}))
        self.mm.data_queue.join()
        self.assertEqual(1, len(video.frames))
        self.assertIs(frame, video.frames[0][0])
        self.assertEqual(timestamp_ns, video.frames[0][1])
        self.assertIsInstance(video.frames[0][2], float)
        self.events.publish.assert_called_once()
        self.assertEqual("meter", self.events.publish.call_args.args[0]["id"])
        self.assertEqual(200, self.client.post("/api/recordings/stop").status_code)
        metadata = json.loads((self.directory / "api" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual({"camera": {"format": "ffv1"}}, metadata["video_options"])
        self.assertEqual(self.settings.model_dump(), metadata["devices"]["camera"][0]["settings"])
        self.assertTrue(video.stopped)

    def test_unsupported_format_is_a_validation_error(self):
        response = self.start_request(video_options={"camera": {"format": "unsupported"}})
        self.assertEqual(422, response.status_code)
        self.assertFalse(self.mm.is_recording())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe are required")
    def test_api_saves_a_real_video_and_frame_csv(self):
        response = self.start_request()
        self.assertEqual(200, response.status_code, response.text)
        for index in range(3):
            self.sampling_queue.put(DeviceData("camera", "frame", get_counter_ns(), synthetic_frame(index)))
        response = self.client.post("/api/recordings/stop")
        self.assertEqual(200, response.status_code, response.text)
        path = self.directory / "api" / "camera.mkv"
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
            capture_output=True, check=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        self.assertEqual(bytes((10, 11, 12, 13, 18, 19, 20, 21, 26, 27, 28, 29)) * 3, decoded)
        metadata = json.loads((path.parent / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual({"state": "completed", "error": None}, metadata["video_results"]["camera"])
        self.assertIsNotNone(metadata["recording_stopped_at"])
        self.assertEqual([], list(path.parent.glob("*.video.json")))
        self.events.publish.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_api_saves_bgr_frames_and_preserves_pixel_format_in_metadata(self):
        self.settings = self.settings.model_copy(update={"pixel_format": "bgr24"})
        self.sampling_queue.put(DeviceData("camera", "settings", get_counter_ns(), self.settings.model_dump()))
        response = self.start_request()
        self.assertEqual(200, response.status_code, response.text)
        image = memoryview(bytes((40, 100, 220)) * (8 * 6)).cast("B", shape=(6, 8, 3))
        self.sampling_queue.put(DeviceData("camera", "frame", get_counter_ns(), CameraFrame(image, 0)))
        response = self.client.post("/api/recordings/stop")
        self.assertEqual(200, response.status_code, response.text)
        path = self.directory / "api" / "camera.mkv"
        decoded = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            capture_output=True, check=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        self.assertEqual(bytes((220, 100, 40)) * (4 * 3), decoded)
        metadata = json.loads((path.parent / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual("bgr24", metadata["devices"]["camera"][0]["settings"]["pixel_format"])
        self.assertEqual({"state": "completed", "error": None}, metadata["video_results"]["camera"])
        self.events.publish.assert_not_called()

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
    def test_full_video_queue_does_not_block_numeric_events_in_common_processor(self):
        with patch.object(VideoRecorder, "QUEUE_MAX_FRAMES", 1):
            response = self.start_request()
        self.assertEqual(200, response.status_code, response.text)
        recorder = self.mm._data_recorder._video_recorders["camera"]
        entered = threading.Event()
        release = threading.Event()
        published = threading.Event()
        self.events.publish.side_effect = lambda event, *, key: published.set()
        original_write = recorder._write_frame

        def delayed_write(pixels):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release writer.")
            original_write(pixels)

        with patch.object(recorder, "_write_frame", side_effect=delayed_write):
            try:
                self.sampling_queue.put(DeviceData("camera", "frame", get_counter_ns(), synthetic_frame()))
                self.assertTrue(entered.wait(2))
                for index in (1, 2):
                    self.sampling_queue.put(DeviceData("camera", "frame", get_counter_ns(), synthetic_frame(index)))
                self.sampling_queue.put(DeviceData("meter", "sample", get_counter_ns(), {"power_w": 0.5}))
                self.assertTrue(published.wait(1), "Common processor was blocked by the full video queue.")
                self.assertIn("queue is full", self.mm.get_recording_status().error)
            finally:
                release.set()
                response = self.client.post("/api/recordings/stop")
        self.assertEqual(503, response.status_code)
        self.assertFalse(self.mm.is_recording())
        metadata = json.loads((self.directory / "api" / "metadata.json").read_text(encoding="utf-8"))
        result = metadata["video_results"]["camera"]
        self.assertEqual("failed", result["state"])
        self.assertIn("queue is full", result["error"])
        self.assertIsNotNone(metadata["recording_stopped_at"])

    def test_camera_requires_explicit_video_format(self):
        response = self.start_request(video_options={})
        self.assertEqual(503, response.status_code)
        self.assertIn("Video format is required", response.json()["detail"])
        self.assertFalse(self.mm.is_recording())

    def test_options_outside_recording_targets_fail_before_data_recorder_start(self):
        with patch.object(self.mm._data_recorder, "start") as start:
            response = self.start_request(video_options={
                "camera": {"format": "ffv1"},
                "other-camera": {"format": "mpeg4"},
            })
        self.assertEqual(503, response.status_code)
        self.assertIn("outside the recording targets", response.json()["detail"])
        start.assert_not_called()
        self.assertFalse(self.mm.is_recording())
        self.assertFalse((self.directory / "api").exists())

    def test_missing_encoder_cannot_report_recording_success(self):
        with patch("backend.app.services.data_racorder.shutil.which", return_value=None):
            response = self.start_request()
        self.assertEqual(503, response.status_code)
        self.assertIn("FFmpeg", response.json()["detail"])
        self.assertFalse(self.mm.is_recording())

    def test_queue_boundary_copies_options_and_uses_preceding_settings_notification(self):
        entered = threading.Event()
        release = threading.Event()
        original_start = self.mm._data_recorder.start
        errors = []
        options = {"camera": VideoRecordingOptions(format="ffv1")}

        def delayed_start(*args, **kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("Test did not release recording start.")
            return original_start(*args, **kwargs)

        def start_recording():
            try:
                self.mm.start_recording(str(self.directory), "boundary", video_options=options)
            except Exception as exc:
                errors.append(exc)

        with patch("backend.app.services.data_racorder.VideoRecorder", FrameSink), patch.object(
            self.mm._data_recorder, "start", side_effect=delayed_start,
        ):
            worker = threading.Thread(target=start_recording)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                options["camera"] = VideoRecordingOptions(format="mpeg4")
                self.settings.exposure = 0.1
                self.sampling_queue.put(DeviceData("camera", "settings", get_counter_ns(), self.settings.model_dump()))
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.mm.data_queue.join()
        video = self.mm._data_recorder._video_recorders["camera"]
        self.assertEqual("ffv1", video.options.format)
        self.assertEqual(0.05, video.settings.exposure)
        history = self.mm.get_metadata().devices["camera"]
        self.assertEqual([0.05, 0.1], [entry["settings"]["exposure"] for entry in history])
        self.camera.get_settings.assert_not_called()


if __name__ == "__main__":
    unittest.main()
