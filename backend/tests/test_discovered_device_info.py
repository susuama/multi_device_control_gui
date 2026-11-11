"""探索結果と管理用の機器情報を、接続せずに区別できることを確認する。"""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pylablib.devices.uc480.uc480 import TCameraInfo, TDeviceInfo

from backend.app.api.devices import router
from backend.app.devices.camera.Thorlabs_uc480 import UC480
from backend.app.models.devices import DiscoveredDevice
from backend.app.services.device_manager import DeviceManager


class DiscoveredDeviceInfoTests(unittest.TestCase):
    def setUp(self):
        self.manager = DeviceManager([UC480])
        self.camera = TCameraInfo(1, 7, 11, "C1285R12M", "4102906151", False, 0)
        self.scan = self.enterContext(patch(
            "backend.app.devices.camera.Thorlabs_uc480.list_cameras", return_value=[self.camera]
        ))
        self.sdk = self.enterContext(patch("backend.app.devices.camera.Thorlabs_uc480.UC480Camera"))
        self.device_id = "UC480_4102906151"

    def test_scan_and_get_do_not_call_unconnected_device_info(self):
        app = FastAPI()
        app.include_router(router)
        app.state.device_manager = self.manager
        with patch.object(UC480, "get_info", side_effect=AssertionError("未接続の情報取得")), TestClient(app) as client:
            response = client.post("/api/devices/scan")
            self.assertEqual(response.status_code, 200)
            discovered = response.json()[0]
            self.assertEqual(discovered["driver"], "UC480")
            self.assertEqual(discovered["model"], "C1285R12M")
            self.assertNotIn("status", discovered)
            self.assertNotIn("id", discovered)
            info = client.get("/api/devices").json()[0]
            self.assertEqual(info["id"], self.device_id)
            self.assertEqual(info["model"], "C1285R12M")
            self.assertEqual(info["status"], "disconnected")
            self.assertEqual(client.get(f"/api/devices/{self.device_id}").json(), info)
            # 再探索の既存機器更新でもget_info()を呼ばない。
            self.assertEqual(client.post("/api/devices/scan").status_code, 200)
            self.assertEqual(client.get("/api/devices/missing").status_code, 404)
        self.sdk.assert_not_called()

    def test_manager_returns_discovered_devices(self):
        result = self.manager.scan()
        self.assertIsInstance(result[0], DiscoveredDevice)
        self.assertEqual(result[0].model, "C1285R12M")

    def test_connected_sdk_information_takes_priority(self):
        self.manager.scan()
        device = self.manager.get(self.device_id)
        device.connected = True
        device._device_info = TDeviceInfo(1, "sdk-model", "Thorlabs", "4102906151", "", "", "", 0)
        self.assertEqual(self.manager.get_info(self.device_id).model, "sdk-model")
        device.connected = False
        self.assertEqual(self.manager.get_info(self.device_id).model, "C1285R12M")

    def test_rescan_updates_metadata_and_stopped_address(self):
        self.manager.scan()
        self.scan.return_value = [self.camera._replace(dev_id=9, model="updated-model")]
        self.manager.scan()
        info = self.manager.get_info(self.device_id)
        self.assertEqual(info.model, "updated-model")
        self.assertEqual(info.address, "9")

    def test_missing_device_keeps_last_discovered_information(self):
        self.manager.scan()
        self.scan.return_value = []
        self.assertEqual(self.manager.scan(), [])
        self.assertEqual(self.manager.get_info(self.device_id).model, "C1285R12M")
        self.assertEqual(self.manager.get_info(self.device_id).status, "disconnected")

    def test_error_state_does_not_require_device_info(self):
        self.manager.scan()
        device = self.manager.get(self.device_id)
        device.last_error = "acquisition failed"
        with patch.object(device, "get_info", side_effect=AssertionError("未接続の情報取得")):
            self.assertEqual(self.manager.get_info(self.device_id).status, "error")

    def test_unregister_removes_discovery_cache(self):
        self.manager.scan()
        self.manager.unregister(self.device_id)
        self.assertNotIn(self.device_id, self.manager._discovered)
        with self.assertRaises(KeyError):
            self.manager.get_info(self.device_id)


if __name__ == "__main__":
    unittest.main()
