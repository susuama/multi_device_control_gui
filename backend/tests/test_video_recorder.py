"""実FFmpegでの保存・読み戻しと、保存Queueの上限・所有権を検証する。"""

import csv
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.app.models.devices import CameraSetting
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.models.recordings import RecordingTarget, VideoRecordingOptions
from backend.app.services.data_racorder import DataRecorder, VideoRecorder


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe are required")
class VideoRecorderTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="video-recorder-tests-")))
        self.settings = CameraSetting(width=32, height=24, fps=10, exposure=0.05, gain=1)
        self.recorders = []
        self.addCleanup(self.stop_recorders)

    def stop_recorders(self):
        for recorder in self.recorders:
            try:
                recorder.stop()
            except RuntimeError:
                # 異常終了を意図したテストでも、すべてのスレッドを回収する。
                pass
            self.assertFalse(recorder._thread is not None and recorder._thread.is_alive())

    def recorder(self, format_name="ffv1", settings=None, name="camera"):
        options = VideoRecordingOptions(format=format_name)
        recorder = VideoRecorder(self.directory / (name + options.extension), settings or self.settings, options)
        self.recorders.append(recorder)
        return recorder

    def test_process_without_worker_is_retained_and_killed_on_stop(self):
        recorder = self.recorder()
        process = Mock()
        process.poll.return_value = None
        process.kill.side_effect = lambda: setattr(process.poll, "return_value", 0)
        recorder._ffmpeg_process = process
        self.assertTrue(recorder.has_pending_cleanup())
        recorder.stop()
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=recorder.STOP_TIMEOUT)
        self.assertFalse(recorder.has_pending_cleanup())

    def image(self, index=0):
        pixels = bytearray((value + index * 37) % 256 for value in range(self.settings.width * self.settings.height))
        return memoryview(pixels).cast("B", shape=(self.settings.height, self.settings.width))

    def run_command(self, args):
        return subprocess.run(
            args, capture_output=True, check=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout

    def read_video(self, path, pixel_format="gray"):
        info = json.loads(self.run_command([
            "ffprobe", "-v", "error", "-count_frames", "-show_entries",
            "stream=codec_name,codec_tag_string,width,height,nb_read_frames,avg_frame_rate", "-of", "json", str(path),
        ]))["streams"][0]
        pixels = self.run_command(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", pixel_format, "pipe:1"])
        return info, pixels

    def color_image(self, pixel_format="rgb24", index=0):
        """各色に異なる全256階調を含む、RGBまたはBGRの配列を作成する。"""
        pixels = bytearray()
        for value in range(self.settings.width * self.settings.height):
            rgb = ((value + index * 37) % 256, (value * 3 + 53 + index * 19) % 256, (value * 7 + 101 + index * 11) % 256)
            pixels.extend(rgb if pixel_format == "rgb24" else rgb[::-1])
        return memoryview(pixels).cast("B", shape=(self.settings.height, self.settings.width, 3))

    def test_rgb_and_bgr_lossless_preserve_colors_for_full_image_and_odd_roi(self):
        for pixel_format in ("rgb24", "bgr24"):
            for format_name in ("h264_lossless", "ffv1"):
                for roi in (None, {"x": 3, "y": 2, "width": 19, "height": 15}):
                    with self.subTest(pixel_format=pixel_format, format=format_name, roi=roi):
                        settings = CameraSetting(**(self.settings.model_dump() | {"pixel_format": pixel_format, "roi": roi}))
                        name = f"{pixel_format}-{format_name}-{'roi' if roi else 'full'}"
                        recorder = self.recorder(format_name, settings, name)
                        recorder.start()
                        expected = bytearray()
                        x, y, width, height = (3, 2, 19, 15) if roi else (0, 0, 32, 24)
                        for index in range(3):
                            rgb = self.color_image("rgb24", index)
                            expected.extend(rgb[row, col, channel] for row in range(y, y + height)
                                            for col in range(x, x + width) for channel in range(3))
                            self.assertTrue(recorder.write(CameraFrame(self.color_image(pixel_format, index), index), 100 + index, index / 10))
                        recorder.stop()
                        info, decoded = self.read_video(recorder.path, "rgb24")
                        self.assertEqual((width, height, "3"), (info["width"], info["height"], info["nb_read_frames"]))
                        self.assertEqual(bytes(expected), decoded)
                        self.assertEqual(["0", "1", "2"], [row["frame_index"] for row in self.read_rows(recorder)])

    def assert_ffv1_round_trip(self, settings, name):
        """保存ROIの全画素と、3フレームの枚数・順序を実FFmpegで照合する。

        Args:
            settings (CameraSetting): 元画像の寸法・画素形式と保存ROI。
            name (str): テスト用の動画ファイル名。拡張子は保存形式から付ける。
        """
        self.settings = settings
        recorder = self.recorder(settings=settings, name=name)
        recorder.start()
        roi = settings.roi
        x, y, width, height = (roi.x, roi.y, roi.width, roi.height) if roi else (0, 0, settings.width, settings.height)
        expected = bytearray()
        for index in range(3):
            if settings.pixel_format == "mono8":
                image = self.image(index)
                expected.extend(image[row, col] for row in range(y, y + height) for col in range(x, x + width))
            else:
                image = self.color_image(settings.pixel_format, index)
                expected.extend(image[row, col, channel] for row in range(y, y + height)
                                for col in range(x, x + width) for channel in range(3))
            self.assertTrue(recorder.write(CameraFrame(image, index), index + 100, index / settings.fps))
        recorder.stop()
        info, decoded = self.read_video(recorder.path, "gray" if settings.pixel_format == "mono8" else settings.pixel_format)
        self.assertEqual((width, height, "3"), (info["width"], info["height"], info["nb_read_frames"]))
        self.assertEqual(bytes(expected), decoded)
        self.assertEqual(["0", "1", "2"], [row["frame_index"] for row in self.read_rows(recorder)])
        self.assertEqual({"state": "completed", "error": None}, recorder.get_result())

    def test_ffv1_preserves_pixels_at_slice_size_boundaries_and_large_dimensions(self):
        for width, height in ((4, 3), (32, 24), (352, 288), (353, 288), (352, 289), (640, 480), (641, 479), (64, 1024)):
            with self.subTest(width=width, height=height):
                settings = CameraSetting(width=width, height=height, fps=10, exposure=0.05, gain=1)
                self.assert_ffv1_round_trip(settings, f"mono-{width}x{height}")

    def test_ffv1_uses_saved_roi_dimensions_and_preserves_large_color_frames(self):
        for pixel_format in ("mono8", "rgb24", "bgr24"):
            for name, width, height, roi in (
                ("full", 640, 480, None),
                ("tiny-roi", 640, 480, {"x": 3, "y": 2, "width": 4, "height": 3}),
                ("tall-roi", 80, 1030, {"x": 3, "y": 2, "width": 64, "height": 1024}),
            ):
                with self.subTest(pixel_format=pixel_format, roi=name):
                    settings = CameraSetting(width=width, height=height, fps=10, exposure=0.05, gain=1,
                        pixel_format=pixel_format, roi=roi)
                    self.assert_ffv1_round_trip(settings, f"{pixel_format}-{name}")

    def test_rgb_and_bgr_mpeg4_keep_color_channel_order(self):
        for pixel_format in ("rgb24", "bgr24"):
            with self.subTest(pixel_format=pixel_format):
                settings = self.settings.model_copy(update={"pixel_format": pixel_format})
                recorder = self.recorder("mpeg4", settings, pixel_format)
                recorder.start()
                # RGB/BGRの取り違えが分かる、色差の大きい単色画像を使う。
                for index, rgb in enumerate(((230, 40, 20), (20, 220, 40), (30, 20, 230))):
                    packed = rgb if pixel_format == "rgb24" else rgb[::-1]
                    image = memoryview(bytes(packed) * (32 * 24)).cast("B", shape=(24, 32, 3))
                    recorder.write(CameraFrame(image, index), index, None)
                recorder.stop()
                info, decoded = self.read_video(recorder.path, "rgb24")
                self.assertEqual((32, 24, "3", "mp4v"), (info["width"], info["height"], info["nb_read_frames"], info["codec_tag_string"]))
                self.assertEqual(32 * 24 * 3 * 3, len(decoded))
                for index, channel in enumerate((0, 1, 2)):
                    frame = decoded[index * 32 * 24 * 3:(index + 1) * 32 * 24 * 3]
                    averages = [sum(frame[offset::3]) / (32 * 24) for offset in range(3)]
                    self.assertGreater(averages[channel], max(averages[offset] for offset in range(3) if offset != channel) + 100)

    def test_color_roi_owns_a_copy_after_source_buffer_is_reused(self):
        settings = CameraSetting(**(self.settings.model_dump() | {
            "pixel_format": "bgr24", "roi": {"x": 3, "y": 2, "width": 19, "height": 15},
        }))
        recorder = self.recorder(settings=settings)
        recorder.start()
        entered = threading.Event()
        release = threading.Event()
        original_write = recorder._write_frame

        def delayed_write(pixels):
            self.assertEqual(19 * 15 * 3, len(pixels))
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release writer.")
            original_write(pixels)

        image = self.color_image("bgr24")
        expected = bytes(image[y, x, channel] for y in range(2, 17) for x in range(3, 22) for channel in range(3))
        with patch.object(recorder, "_write_frame", side_effect=delayed_write):
            try:
                recorder.write(CameraFrame(image, 0), 1, None)
                self.assertTrue(entered.wait(2))
                image.cast("B")[:] = bytes(image.nbytes)
            finally:
                release.set()
            recorder.stop()
        self.assertEqual(expected, self.read_video(recorder.path, "bgr24")[1])

    def test_color_queue_memory_limit_uses_three_bytes_per_roi_pixel(self):
        for pixel_format in ("rgb24", "bgr24"):
            with self.subTest(pixel_format=pixel_format):
                settings = CameraSetting(**(self.settings.model_dump() | {
                    "pixel_format": pixel_format, "roi": {"x": 1, "y": 1, "width": 10, "height": 8},
                }))
                recorder = self.recorder(settings=settings, name=pixel_format)
                recorder.QUEUE_MAX_BYTES = 10 * 8 * 3 * 2
                recorder.start()
                self.assertEqual(2, recorder._queue.maxsize)
                recorder.stop()

    def test_color_image_shape_dtype_and_contiguity_are_checked(self):
        settings = self.settings.model_copy(update={"pixel_format": "rgb24"})
        recorder = self.recorder(settings=settings)
        recorder.start()
        for image in (self.image(), memoryview(bytes(24 * 32 * 4)).cast("B", shape=(24, 32, 4)),
                      memoryview(bytes(24 * 32 * 3 * 2)).cast("H", shape=(24, 32, 3))):
            with self.subTest(shape=image.shape), self.assertRaisesRegex(ValueError, "rgb24"):
                recorder.write(CameraFrame(image, 0), 1, None)
        recorder.stop()
        settings = self.settings.model_copy(update={"pixel_format": "rgb24", "height": 12})
        recorder = self.recorder(settings=settings, name="strided")
        recorder.start()
        with self.assertRaisesRegex(ValueError, "C-contiguous"):
            recorder.write(CameraFrame(self.color_image()[::2], 0), 1, None)
        recorder.stop()

    def read_rows(self, recorder):
        with recorder.frame_path.open(encoding="utf-8-sig", newline="") as file:
            return list(csv.DictReader(file))

    def test_three_formats_preserve_shape_count_order_and_lossless_pixels(self):
        for format_name, codec in (("h264_lossless", "h264"), ("ffv1", "ffv1"), ("mpeg4", "mpeg4")):
            with self.subTest(format=format_name):
                settings = self.settings.model_copy(update={"roi": None})
                # 奇数のROI幅・高さも、拡大縮小や画素の追加なしで記録する。
                settings = CameraSetting(**(settings.model_dump() | {"roi": {"x": 3, "y": 2, "width": 19, "height": 15}}))
                recorder = self.recorder(format_name, settings, name=format_name)
                recorder.start()
                expected = b""
                for index, timestamp in enumerate((100, 300, 900, 1000)):
                    image = self.image(index)
                    flat = image.cast("B")
                    expected += b"".join(flat[y * 32 + 3:y * 32 + 22] for y in range(2, 17))
                    self.assertTrue(recorder.write(CameraFrame(image, index * 2, 500 + index), timestamp, index * 0.1))
                recorder.stop()
                info, pixels = self.read_video(recorder.path)
                self.assertEqual(codec, info["codec_name"])
                self.assertEqual((19, 15, "4"), (info["width"], info["height"], info["nb_read_frames"]))
                self.assertEqual("10/1", info["avg_frame_rate"])
                self.assertEqual(len(expected), len(pixels))
                if format_name != "mpeg4":
                    self.assertEqual(expected, pixels)
                else:
                    self.assertEqual("mp4v", info["codec_tag_string"])
                rows = self.read_rows(recorder)
                self.assertEqual(
                    ["video_frame_index", "frame_index", "timestamp_ns", "device_timestamp_ns", "elapsed_s"],
                    list(rows[0]),
                )
                self.assertEqual(["0", "1", "2", "3"], [row["video_frame_index"] for row in rows])
                self.assertEqual(["0", "2", "4", "6"], [row["frame_index"] for row in rows])
                self.assertEqual(["100", "300", "900", "1000"], [row["timestamp_ns"] for row in rows])
                self.assertEqual(["500", "501", "502", "503"], [row["device_timestamp_ns"] for row in rows])
                self.assertEqual({"state": "completed", "error": None}, recorder.get_result())
                self.assertEqual((4, 4), (recorder.accepted_frames, recorder.written_frames))
                self.assertEqual([], list(self.directory.glob("*.video.json")))

    def test_full_image_lossless_preserves_all_256_levels(self):
        for format_name in ("h264_lossless", "ffv1"):
            with self.subTest(format=format_name):
                recorder = self.recorder(format_name, name=format_name)
                recorder.start()
                image = self.image()
                expected = image.tobytes()
                self.assertTrue(recorder.write(CameraFrame(image, 1), 1234, None))
                recorder.stop()
                info, decoded = self.read_video(recorder.path)
                self.assertEqual((32, 24), (info["width"], info["height"]))
                self.assertEqual(expected, decoded)
                self.assertEqual({
                    "video_frame_index": "0", "frame_index": "1", "timestamp_ns": "1234",
                    "device_timestamp_ns": "", "elapsed_s": "",
                }, self.read_rows(recorder)[0])

    def test_source_buffer_can_be_reused_after_roi_is_copied(self):
        settings = CameraSetting(**(self.settings.model_dump() | {"roi": {"x": 3, "y": 2, "width": 19, "height": 15}}))
        recorder = self.recorder(settings=settings)
        recorder.start()
        entered = threading.Event()
        release = threading.Event()
        original_write = recorder._write_frame

        def delayed_write(pixels):
            self.assertEqual(19 * 15, len(pixels))
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release writer.")
            original_write(pixels)

        image = self.image()
        flat = image.cast("B")
        expected = b"".join(flat[y * 32 + 3:y * 32 + 22] for y in range(2, 17))
        with patch.object(recorder, "_write_frame", side_effect=delayed_write):
            try:
                recorder.write(CameraFrame(image, 0), 100, 0.0)
                self.assertTrue(entered.wait(2))
                image.cast("B")[:] = bytes(image.nbytes)
            finally:
                release.set()
            recorder.stop()
        self.assertEqual(expected, self.read_video(recorder.path)[1])

    def test_full_queue_returns_promptly_and_numeric_recording_continues(self):
        data_recorder = DataRecorder()
        targets = {
            "meter": RecordingTarget(type="power_meter", settings={}),
            "camera": RecordingTarget(type="camera", settings=self.settings.model_dump(), video_options=VideoRecordingOptions(format="ffv1")),
        }
        with patch.object(VideoRecorder, "QUEUE_MAX_FRAMES", 1):
            data_recorder.start(str(self.directory), "slow", targets=targets)
        recorder = data_recorder._video_recorders["camera"]
        self.recorders.append(recorder)
        entered = threading.Event()
        release = threading.Event()
        original_write = recorder._write_frame

        def delayed_write(pixels):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release writer.")
            original_write(pixels)

        with patch.object(recorder, "_write_frame", side_effect=delayed_write):
            try:
                data_recorder.write(DeviceData("camera", "frame", 100, CameraFrame(self.image(), 0)))
                self.assertTrue(entered.wait(2))
                data_recorder.write(DeviceData("camera", "frame", 200, CameraFrame(self.image(), 1)))
                started = time.monotonic()
                data_recorder.write(DeviceData("camera", "frame", 300, CameraFrame(self.image(), 2)))
                data_recorder.write(DeviceData("meter", "sample", 350, {"power_w": 0.5}))
                self.assertLess(time.monotonic() - started, 0.2)
                self.assertTrue(data_recorder.is_recording())
                self.assertIn("queue is full", data_recorder.get_status().error)
            finally:
                release.set()
                with self.assertRaisesRegex(RuntimeError, "queue is full"):
                    data_recorder.stop()
                    data_recorder.wait_stopped()
        data_recorder.write_metadata({"recording_stopped_at": "test-stop-time"})
        metadata = json.loads((self.directory / "slow" / "metadata.json").read_text(encoding="utf-8"))
        result = metadata["video_results"]["camera"]
        self.assertEqual("failed", result["state"])
        self.assertIn("queue is full", result["error"])
        self.assertEqual((2, 2), (recorder.accepted_frames, recorder.written_frames))
        with (self.directory / "slow" / "meter.csv").open(encoding="utf-8-sig", newline="") as file:
            self.assertEqual("0.5", next(csv.DictReader(file))["power_w"])
        self.assertEqual("2", self.read_video(recorder.path)[0]["nb_read_frames"])

    def test_image_shape_and_pixel_type_are_checked(self):
        recorder = self.recorder()
        recorder.start()
        for image in (memoryview(bytes(768)), memoryview(bytes(768)).cast("B", shape=(16, 48)), memoryview(bytes(1536)).cast("H", shape=(24, 32))):
            with self.subTest(shape=image.shape), self.assertRaisesRegex(ValueError, "mono8"):
                recorder.write(CameraFrame(image, 0), 1, None)
        recorder.stop()
        self.assertEqual(0, recorder.accepted_frames)

    def test_queue_memory_limit_is_applied_in_addition_to_frame_count(self):
        recorder = self.recorder()
        recorder.QUEUE_MAX_BYTES = 32 * 24 * 2
        recorder.start()
        self.assertEqual(2, recorder._queue.maxsize)
        recorder.stop()

    def test_oversized_frame_is_rejected_before_files_are_created(self):
        recorder = self.recorder()
        recorder.QUEUE_MAX_BYTES = 1
        with self.assertRaisesRegex(ValueError, "memory limit"):
            recorder.start()
        self.assertEqual([], list(self.directory.iterdir()))

    def test_unsupported_encoder_is_reported_as_recording_failure(self):
        recorder = self.recorder()
        command = recorder._build_ffmpeg_command(shutil.which("ffmpeg"))
        command[command.index("-c:v") + 1] = "missing_test_codec"
        with patch.object(recorder, "_build_ffmpeg_command", return_value=command):
            recorder.start()
        with self.assertRaises(RuntimeError):
            recorder.stop()
        self.assertEqual("failed", recorder.get_result()["state"])
        self.assertTrue(recorder._ffmpeg_process.stdin.closed)
        self.assertEqual([], list(self.directory.glob("*.video.json")))

    def test_existing_video_and_frame_csv_are_never_overwritten(self):
        for suffix in ("", ".frames.csv"):
            with self.subTest(suffix=suffix):
                recorder = self.recorder(name="existing" + str(len(suffix)))
                existing = Path(str(recorder.path) + suffix)
                existing.write_bytes(b"existing measurement")
                with self.assertRaises(FileExistsError):
                    recorder.start()
                self.assertEqual(b"existing measurement", existing.read_bytes())

    def test_sidecar_name_collision_keeps_numeric_csv_separate(self):
        recorder = DataRecorder()
        try:
            recorder.start(str(self.directory), "collision", targets={
                "camera": RecordingTarget(type="camera", settings=self.settings.model_dump(), video_options=VideoRecordingOptions(format="ffv1")),
                "camera.mkv.frames": RecordingTarget(type="power_meter", settings={}),
            })
            recorder.write(DeviceData("camera", "frame", 100, CameraFrame(self.image(), 0)))
            recorder.write(DeviceData("camera.mkv.frames", "sample", 101, {"power_w": 1.0}))
        finally:
            recorder.stop()
            recorder.wait_stopped()
        self.assertTrue((self.directory / "collision" / "camera.mkv.frames.csv").exists())
        self.assertTrue((self.directory / "collision" / "camera.mkv.frames_2.csv").exists())

    def test_failed_encoder_is_reported_and_worker_is_reaped(self):
        recorder = self.recorder()
        recorder.start()
        recorder._ffmpeg_process.kill()
        recorder._ffmpeg_process.wait(timeout=5)
        recorder.write(CameraFrame(self.image(), 0), 1, None)
        with self.assertRaises(RuntimeError):
            recorder.stop()
        result = recorder.get_result()
        self.assertEqual("failed", result["state"])
        self.assertIsNotNone(result["error"])
        self.assertFalse(recorder._thread.is_alive())
        self.assertTrue(recorder._ffmpeg_process.stdin.closed)

    def test_exited_encoder_is_detected_before_dequeuing_pending_frame(self):
        recorder = self.recorder()
        entered = threading.Event()
        release = threading.Event()
        original_run = recorder._run

        def delayed_run():
            entered.set()
            release.wait(5)
            original_run()

        with patch.object(recorder, "_run", side_effect=delayed_run):
            try:
                recorder.start()
                self.assertTrue(entered.wait(2))
                self.assertTrue(recorder.write(CameraFrame(self.image(), 0), 1, None))
                recorder._ffmpeg_process.kill()
                recorder._ffmpeg_process.wait(timeout=5)
            finally:
                release.set()
                with self.assertRaisesRegex(RuntimeError, "FFmpeg exited before video recording stopped"):
                    recorder.stop()
        self.assertEqual(0, recorder.written_frames)
        self.assertEqual("failed", recorder.get_result()["state"])


if __name__ == "__main__":
    unittest.main()
