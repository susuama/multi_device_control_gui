"""PM160の設定適用と測定を、実機通信を行わずに確認する。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from backend.app.devices.power_meter.Thorlabs_PM100A import PM160Device


class PM160DeviceTests(unittest.TestCase):
    def setUp(self):
        self.device = PM160Device("pm160", address="USB0::PM160::INSTR")
        self.pm160 = Mock()
        self.pm160.is_opened.return_value = True
        self.pm160.get_wavelength.return_value = 780e-9
        self.pm160.get_range.return_value = 0.01
        self.pm160.is_autorange_enabled.return_value = False
        self.pm160.ask.side_effect = lambda command, **kwargs: (
            True if command == "Input:Pdiode:Filter:Lpass:STATe?" else 8
        )
        self.device.pm160 = self.pm160

    def settings(self, **updates):
        values = dict(wavelength=785.0, range=0.012, auto_range=False, bandwidth="low", averaging=10)
        values.update(updates)
        return type(self.device.settings)(**values)

    def test_manual_settings_return_confirmed_values_in_public_units(self):
        applied = self.device.set_settings(self.settings())

        self.pm160.set_wavelength.assert_called_once()
        self.assertAlmostEqual(self.pm160.set_wavelength.call_args.args[0], 785e-9)
        self.pm160.enable_autorange.assert_called_once_with(False, sensor_mode="power")
        self.pm160.set_range.assert_called_once_with(0.012, sensor_mode="power")
        self.assertLess(
            self.pm160.mock_calls.index(call.enable_autorange(False, sensor_mode="power")),
            self.pm160.mock_calls.index(call.set_range(0.012, sensor_mode="power")),
        )
        self.pm160.write.assert_has_calls([
            call("Input:Pdiode:Filter:Lpass:STATe", True, arg_type="bool"),
            call("Sense:Average:COUNt", 10, arg_type="int"),
        ])
        self.assertAlmostEqual(applied.wavelength, 780.0)
        self.assertEqual((applied.range, applied.auto_range, applied.bandwidth, applied.averaging),
                         (0.01, False, "low", 8))
        self.assertIs(self.device.settings, applied)

    def test_auto_range_does_not_write_manual_range(self):
        self.pm160.is_autorange_enabled.return_value = True
        self.pm160.ask.side_effect = lambda command, **kwargs: (
            False if command == "Input:Pdiode:Filter:Lpass:STATe?" else 8
        )
        applied = self.device._apply_settings(self.settings(auto_range=True, bandwidth="high"))
        self.pm160.enable_autorange.assert_called_once_with(True, sensor_mode="power")
        self.pm160.set_range.assert_not_called()
        self.assertTrue(applied.auto_range)
        self.assertEqual(applied.bandwidth, "high")
        self.assertIn(call("Input:Pdiode:Filter:Lpass:STATe", False, arg_type="bool"),
                      self.pm160.write.call_args_list)

    def test_invalid_settings_do_not_start_writing(self):
        for updates in ({"wavelength": 0}, {"wavelength": float("nan")},
                        {"range": -1}, {"bandwidth": "invalid"}, {"averaging": 0}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                self.device._apply_settings(self.settings(**updates))
        self.pm160.set_wavelength.assert_not_called()
        self.pm160.write.assert_not_called()

    def test_setting_failure_preserves_cached_settings_and_propagates(self):
        original = self.device.settings
        failure = OSError("USB write failed")
        self.pm160.write.side_effect = failure
        with self.assertRaises(OSError) as raised:
            self.device.set_settings(self.settings())
        self.assertIs(raised.exception, failure)
        self.assertIs(self.device.settings, original)

    def test_readback_failure_preserves_cached_settings(self):
        original = self.device.settings
        self.pm160.get_range.side_effect = TimeoutError("Readback timed out")
        with self.assertRaises(TimeoutError):
            self.device.set_settings(self.settings())
        self.assertIs(self.device.settings, original)

    def test_open_selects_power_mode_once_and_samples_are_new_measurements(self):
        self.device.pm160 = None
        self.pm160.get_device_info.return_value = SimpleNamespace(
            name="PM160", manufacturer="Thorlabs", serial="12345"
        )
        self.pm160.get_reading.side_effect = [0.0012, 0.0013]
        with patch("backend.app.devices.power_meter.Thorlabs_PM160.PM160", return_value=self.pm160):
            self.assertTrue(self.device.connect())
        self.assertEqual(self.device._read_sample(), {"power_w": 0.0012})
        self.assertEqual(self.device._read_sample(), {"power_w": 0.0013})
        self.pm160.set_sensor_mode.assert_called_once_with("power")
        self.assertEqual(self.pm160.get_reading.call_args_list, [
            call(sensor_mode=None, measure=True, overrng="error"),
            call(sensor_mode=None, measure=True, overrng="error"),
        ])

    def test_measurement_failure_is_not_replaced_with_dummy_value(self):
        self.pm160.get_reading.side_effect = OSError("USB read failed")
        with self.assertRaisesRegex(OSError, "USB read failed"):
            self.device._read_sample()

    def test_disconnected_device_rejects_read_and_settings(self):
        self.device.pm160 = None
        for operation in (self.device._get_settings, self.device._read_sample,
                          lambda: self.device._apply_settings(self.settings())):
            with self.subTest(operation=operation), self.assertRaises(ConnectionError):
                operation()
        self.pm160.write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
