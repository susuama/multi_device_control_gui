"""機器別の手動テストを、ダミー機器で確認する。実機には接続しない。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.app.devices.camera.dummy_camera import DummyCamera
from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.models.devices import CameraSetting
from backend.app.models.measurement import CameraFrame
from backend.tests.manual import check_pm100a, check_uc480
from backend.tests.manual.hardware_check_common import positive_seconds, save_frame


class SimulatedPowerMeter(DummyPowerMeter):
    def is_connected(self):
        return self.connected


class SimulatedCamera(DummyCamera):
    def is_connected(self):
        return self.connected


class HardwareCheckRunnerTests(unittest.TestCase):
    def run_manual(self, module, driver_name, device, connection_name):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module, "SCAN_ONLY", False), patch.object(
                module, connection_name, "simulation"
            ), patch.object(module, "CAPTURE_SECONDS", 0.6), patch.object(
                module, "OUTPUT_ROOT", Path(directory)
            ), patch.object(module, driver_name, return_value=device):
                exit_code = module.main()

            path = next(Path(directory).glob("*/*/result.json"))
            result = json.loads(path.read_text(encoding="utf-8"))
            log = (path.parent / "test.log").read_text(encoding="utf-8")
            self.assertIn("切断完了", log)

            if result["passed"]:
                self.assertEqual(len(result["cycles"]), 2)
                self.assertGreater(result["cycles"][0]["count_after_reapply"], 0)
                if module is check_uc480:
                    self.assertEqual(len(list(path.parent.glob("uc480-cycle-*.png"))), 2)

        self.assertFalse(device.connected)
        self.assertFalse(device.is_sampling())
        return exit_code, result

    def test_pm160_file_runs_without_command_line_arguments(self):
        code, result = self.run_manual(
            check_pm100a, "PM160Device", SimulatedPowerMeter("simulation"), "VISA_ADDRESS"
        )
        self.assertEqual(code, 0)
        self.assertTrue(result["passed"])

    def test_camera_file_runs_without_command_line_arguments(self):
        code, result = self.run_manual(
            check_uc480, "uc480Camera", SimulatedCamera("simulation"), "CAMERA_SERIAL"
        )
        self.assertEqual(code, 0)
        self.assertTrue(result["passed"])

    def test_failure_records_stage_and_disconnects(self):
        device = SimulatedPowerMeter("simulation")
        with patch.object(device, "_apply_settings", side_effect=OSError("write failed")):
            code, result = self.run_manual(check_pm100a, "PM160Device", device, "VISA_ADDRESS")
        self.assertEqual(code, 1)
        self.assertFalse(result["passed"])
        self.assertEqual(result["error"]["stage"], "停止中の設定再適用")

    def test_scan_only_does_not_create_camera(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            check_uc480, "SCAN_ONLY", True
        ), patch.object(check_uc480, "OUTPUT_ROOT", Path(directory)), patch.object(
            check_uc480, "uc480Camera"
        ) as driver:
            driver.scan.return_value = []
            self.assertEqual(check_uc480.main(), 0)
            driver.scan.assert_called_once()
            driver.assert_not_called()

    def test_png_converts_bgr_to_rgb(self):
        settings = CameraSetting(
            width=1, height=1, fps=10, exposure=0.01, gain=1, pixel_format="bgr24"
        )
        pixels = memoryview(bytes([10, 20, 30])).cast("B", shape=(1, 1, 3))
        frame = CameraFrame(pixels, 1)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.png"
            save_frame(frame, settings, path)
            with Image.open(path) as image:
                self.assertEqual(image.getpixel((0, 0)), (30, 20, 10))

    def test_seconds_reject_nonpositive_and_nonfinite(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                positive_seconds(value)


if __name__ == "__main__":
    unittest.main()
