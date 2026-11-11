"""uc480の探索とDeviceManagerへの登録を、SDKを呼ばずに確認する。"""

import unittest
from unittest.mock import patch

from pylablib.devices.uc480.uc480 import TCameraInfo

from backend.app.devices.camera.Thorlabs_uc480 import uc480Camera
from backend.app.services.device_manager import DeviceManager


class UC480ScanTests(unittest.TestCase):
    def test_in_use_camera_is_retained_with_device_id_not_camera_id(self):
        camera = TCameraInfo(1, 7, 11, "DCC1545M", "SERIAL001", True, 0)
        with patch("backend.app.devices.camera.Thorlabs_uc480.list_cameras", return_value=[camera]) as scan:
            devices = uc480Camera.scan()
        scan.assert_called_once_with(backend="uc480")
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].model_dump(), {
            "driver": "uc480Camera", "type": "camera", "manufacturer": "Thorlabs",
            "model": "DCC1545M", "serial_number": "SERIAL001", "address": "7",
        })

    def test_rescan_updates_address_without_creating_a_second_device(self):
        manager = DeviceManager([uc480Camera])
        first = TCameraInfo(1, 2, 11, "DCC1545M", "SERIAL001", False, 0)
        reconnected = first._replace(dev_id=7)
        with patch("backend.app.devices.camera.Thorlabs_uc480.list_cameras",
                   side_effect=[[first], [reconnected]]):
            manager.scan()
            device = next(iter(manager.get_all().values()))
            manager.scan()
        self.assertEqual(list(manager.get_all()), ["uc480Camera_SERIAL001"])
        self.assertIs(manager.get("uc480Camera_SERIAL001"), device)
        self.assertEqual(device.address, "7")

    def test_sdk_failure_does_not_look_like_devices_disappeared(self):
        failure = OSError("uc480 DLL could not be loaded")
        with patch("backend.app.devices.camera.Thorlabs_uc480.list_cameras", side_effect=failure):
            with self.assertRaises(OSError) as raised:
                uc480Camera.scan()
        self.assertIs(raised.exception, failure)


if __name__ == "__main__":
    unittest.main()
