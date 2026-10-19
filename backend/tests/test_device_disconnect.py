"""切断・停止失敗の回帰テスト。実機やHTTPサーバーは起動しない。"""

import logging
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.main import create_app


class DeviceDisconnectTests(unittest.TestCase):
    def setUp(self):
        # 異常を意図的に起こすテストのログを抑え、接続済みのダミー2台を用意する。
        self.previous_logging_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, self.previous_logging_disable)
        self.addCleanup(self.assert_workers_stopped)
        self.release_events = []
        self.app = create_app()
        # lifespan内でサービスが作られる。終了処理はテスト失敗時も実行する。
        self.client = self.enterContext(TestClient(self.app))
        self.dm = self.app.state.device_manager
        self.mm = self.app.state.measurement_manager
        self.dm.scan()
        self.ids = list(self.dm.get_all())
        self.devices = list(self.dm.get_all().values())
        for device in self.devices:
            device.connect()

    def tearDown(self):
        # テストが途中で失敗しても、模擬通信の待機を解除してスレッドを終了させる。
        for event in self.release_events:
            event.set()

    def assert_workers_stopped(self):
        # TestClientの終了後に、実際の取得・処理スレッドが残っていないことを確認する。
        for thread in threading.enumerate():
            if thread.name.startswith("sampling-") or thread.name == "measurement-processor":
                thread.join(1)
                self.assertFalse(thread.is_alive(), thread.name)
        if hasattr(self, "mm"):
            self.assertIsNone(self.mm._processor_thread)

    def wait_until(self, condition):
        # 固定時間の待機に頼らず、別スレッドの状態更新を期限付きで待つ。
        deadline = time.monotonic() + 1
        while not condition() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(condition())

    def start_blocked_device(self):
        # readが戻らない通信を再現する。解除用イベントはtearDownでも解放する。
        device = self.devices[0]
        entered = threading.Event()
        release = threading.Event()
        self.release_events.append(release)
        real_read = device.read

        def read():
            entered.set()
            release.wait(2)
            return real_read()

        device.read = read
        # 停止タイムアウトの動作を、短い待機時間で確認する。
        device.STOP_TIMEOUT = 0.03
        self.mm.start_sampling(self.ids[0])
        self.assertTrue(entered.wait(1))
        return device, release

    def test_immediate_read_failure_preserves_error_and_processor(self):
        # 取得に失敗しても、共通Processorはアプリ終了まで待機を継続する。
        device = self.devices[0]
        with patch.object(device, "read", side_effect=OSError("device unplugged")):
            self.mm.start_sampling(self.ids[0])
            device._sampling_thread.join(1)
            self.mm.stop_all_sampling()
        self.assertFalse(device.connected)
        self.assertEqual("error", device.get_info().status)
        self.assertEqual("device unplugged", device.last_error)
        self.assertFalse(device.is_sampling())
        self.assertTrue(self.mm._processor_thread.is_alive())
        self.wait_until(lambda: self.mm.data_queue.unfinished_tasks == 0)
        self.assertEqual("error", self.client.get(f"/api/devices/{self.ids[0]}").json()["status"])

    def test_failure_of_one_device_preserves_other_and_measurement_time(self):
        # 片方だけが切断されても、もう片方の取得と共通の開始時刻を維持する。
        for device_id in self.ids:
            self.mm.start_sampling(device_id)
        start = self.mm._start_counter_ns
        thread = self.devices[0]._sampling_thread
        with patch.object(self.devices[0], "read", side_effect=OSError("connection lost")):
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.mm.is_sampling(self.ids[0]))
        self.assertTrue(self.mm.is_sampling(self.ids[1]))
        self.assertEqual(start, self.mm._start_counter_ns)
        self.assertTrue(self.mm._processor_thread.is_alive())
        self.mm.stop_all_sampling()

    def test_explicit_disconnect_stops_device_and_preserves_processor(self):
        # 取得スレッドは機器が管理し、共通Processorは次の取得を待つ。
        self.mm.start_sampling(self.ids[0])
        self.assertTrue(self.dm.disconnect(self.ids[0]))
        self.mm.stop_sampling(self.ids[0])
        self.assertEqual("disconnected", self.devices[0].get_info().status)
        self.assertFalse(self.devices[0].is_sampling())
        self.assertTrue(self.mm._processor_thread.is_alive())

    def test_rescan_retains_missing_device_and_stops_its_worker(self):
        # 測定中の1台がスキャン結果から消えた状況を再現する。
        for device_id in self.ids:
            self.mm.start_sampling(device_id)
        with patch.object(DummyPowerMeter, "scan", return_value=[DummyPowerMeter.scan()[1]]):
            self.dm.scan()
        self.assertIs(self.devices[0], self.dm.get(self.ids[0]))
        self.assertEqual("disconnected", self.devices[0].get_info().status)
        self.assertFalse(self.mm.is_sampling(self.ids[0]))
        self.assertTrue(self.mm.is_sampling(self.ids[1]))
        self.mm.stop_all_sampling()

    def test_address_changes_only_when_disconnected_and_stopped(self):
        # 同じIDの新アドレスは、接続中には反映せず、切断後に既存機器へ反映する。
        found = DummyPowerMeter.scan()
        found[0].address = "new_address"
        self.dm._update_devices(found)
        self.assertEqual("dummy_address_1", self.devices[0].address)
        self.dm.disconnect(self.ids[0])
        self.dm._update_devices(found)
        self.assertIs(self.devices[0], self.dm.get(self.ids[0]))
        self.assertEqual("new_address", self.devices[0].address)

    def test_stop_timeout_preserves_worker_and_all_stop_continues(self):
        # 停止不能な1台の登録を保持しつつ、正常なもう1台は停止できることを確認する。
        device, release = self.start_blocked_device()
        self.mm.start_sampling(self.ids[1])
        original_thread = device._sampling_thread
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            self.mm.stop_sampling(self.ids[0])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertIs(original_thread, device._sampling_thread)
        self.assertEqual("error", device.get_info().status)
        with self.assertRaises(RuntimeError):
            self.mm.stop_all_sampling()
        self.assertFalse(self.mm.is_sampling(self.ids[1]))
        # ドライバーは再起動を拒否し、管理サービスの重複開始も同じスレッドを保つ。
        with self.assertRaises(RuntimeError):
            device.start_sampling(self.mm.data_queue)
        self.mm.start_sampling(self.ids[0])
        with self.assertRaises(RuntimeError):
            device.connect()
        self.assertIs(original_thread, device._sampling_thread)
        release.set()
        original_thread.join(1)
        self.mm.stop_all_sampling()

    def test_rescan_stop_failure_keeps_devices_and_continues(self):
        # 両機器が未検出になっても、片方の停止失敗で他方の停止を中断しない。
        device, release = self.start_blocked_device()
        self.mm.start_sampling(self.ids[1])
        with patch.object(DummyPowerMeter, "scan", return_value=[]):
            with self.assertRaises(RuntimeError):
                self.dm.scan()
        self.assertIs(device, self.dm.get(self.ids[0]))
        self.assertTrue(self.mm.is_sampling(self.ids[0]))
        self.assertFalse(self.mm.is_sampling(self.ids[1]))
        self.assertEqual("error", device.get_info().status)
        release.set()

    def test_invalid_samples_do_not_stop_shared_processor(self):
        # Noneや属性のないデータを終了信号と混同せず、共通処理を継続する。
        self.mm.start_sampling(self.ids[0])
        with patch.object(self.mm.logger, "exception") as log_error:
            self.mm.data_queue.put(None)
            self.mm.data_queue.put(object())
            self.wait_until(lambda: log_error.call_count >= 2)
        self.assertTrue(self.mm._processor_thread.is_alive())
        self.assertTrue(self.mm.is_sampling(self.ids[0]))
        self.mm.stop_all_sampling()
        self.wait_until(lambda: self.mm.data_queue.unfinished_tasks == 0)

    def test_restart_after_complete_stop_uses_new_start_time(self):
        # 全停止後の測定は、以前の測定とは別の開始時刻を使う。
        self.mm.start_sampling(self.ids[0])
        old_start = self.mm._start_counter_ns
        self.mm.stop_all_sampling()
        # 前回のデータ処理のために基準時刻を保持し、再開時に更新する。
        self.assertEqual(old_start, self.mm._start_counter_ns)
        self.mm.start_sampling(self.ids[1])
        self.assertGreater(self.mm._start_counter_ns, old_start)
        self.mm.stop_all_sampling()
        self.wait_until(lambda: self.mm.data_queue.unfinished_tasks == 0)

    def test_invalid_start_preserves_idle_processor_without_sampling(self):
        # 開始に失敗しても共通Processorを重複起動せず、取得スレッドを残さない。
        processor = self.mm._processor_thread
        with self.assertRaises(KeyError):
            self.mm.start_sampling("unknown")
        self.assertIs(processor, self.mm._processor_thread)
        self.dm.disconnect(self.ids[0])
        with self.assertRaises(RuntimeError):
            self.mm.start_sampling(self.ids[0])
        self.mm.stop_all_sampling()
        self.assertIs(processor, self.mm._processor_thread)
        self.assertTrue(processor.is_alive())
        self.assertTrue(all(not device.is_sampling() for device in self.devices))
        self.assertIsNone(self.mm._start_counter_ns)

    def test_restart_waits_for_previous_samples_to_finish_processing(self):
        # 最後のサンプルの時刻変換を待たせ、取得終了後にも処理が残る状況を作る。
        entered = threading.Event()
        release = threading.Event()
        self.release_events.append(release)
        real_elapsed = self.mm._get_elapsed

        def elapsed(counter):
            entered.set()
            release.wait(2)
            return real_elapsed(counter)

        with patch.object(self.mm, "_get_elapsed", side_effect=elapsed):
            self.mm.start_sampling(self.ids[0])
            self.assertTrue(entered.wait(1))
            old_start = self.mm._start_counter_ns
            self.dm.disconnect(self.ids[0])
            self.assertFalse(self.devices[0].is_sampling())
            self.assertEqual(old_start, self.mm._start_counter_ns)

            # 再開要求は前回のQueueの処理を待つ。待機中に基準時刻を変えない。
            completed = threading.Event()
            errors = []

            def restart():
                try:
                    self.mm.start_sampling(self.ids[1])
                except Exception as exc:
                    errors.append(exc)
                finally:
                    completed.set()

            worker = threading.Thread(target=restart, name="test-restart")
            worker.start()
            self.addCleanup(worker.join, 1)
            self.assertFalse(completed.wait(0.03))
            self.assertEqual(old_start, self.mm._start_counter_ns)
            release.set()
            self.assertTrue(completed.wait(1))
            worker.join(1)
            self.assertEqual([], errors)
        self.assertGreater(self.mm._start_counter_ns, old_start)
        self.mm.stop_all_sampling()
        self.wait_until(lambda: self.mm.data_queue.unfinished_tasks == 0)
        self.assertTrue(self.mm._processor_thread.is_alive())

    def test_device_api_success_and_missing_device(self):
        # 実サーバーを起動せず、一覧・スキャン・接続・測定中の切断と404応答を確認する。
        response = self.client.get("/api/devices")
        self.assertEqual(200, response.status_code)
        self.assertEqual(set(self.ids), {item["id"] for item in response.json()})
        self.assertEqual(200, self.client.post("/api/devices/scan").status_code)
        device_id = self.ids[0]
        self.mm.start_sampling(device_id)
        response = self.client.post(f"/api/devices/{device_id}/disconnect")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"success": True}, response.json())
        self.assertEqual("disconnected", self.client.get(f"/api/devices/{device_id}").json()["status"])
        self.assertEqual({"success": True}, self.client.post(f"/api/devices/{device_id}/connect").json())
        for method, suffix in (("GET", ""), ("POST", "/connect"), ("POST", "/disconnect")):
            self.assertEqual(404, self.client.request(method, "/api/devices/unknown" + suffix).status_code)

    def test_device_settings_api_returns_and_updates_settings(self):
        # 現在の設定を取得し、設定一式の更新結果をAPIとデバイスの両方から確認する。
        path = f"/api/devices/{self.ids[0]}/settings"
        original = self.dm.get_settings(self.ids[0]).model_dump(mode="json")
        response = self.client.get(path)
        self.assertEqual(200, response.status_code)
        self.assertEqual(original, response.json())

        settings = {
            "wavelength": 800,
            "range": 0.1,
            "auto_range": True,
            "bandwidth": "low",
            "averaging": 1,
        }
        response = self.client.put(path, json=settings)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(settings, response.json())
        self.assertEqual(settings, self.client.get(path).json())
        self.assertEqual(settings, self.dm.get_settings(self.ids[0]).model_dump(mode="json"))

    def test_device_settings_api_returns_404_for_unknown_device(self):
        settings = self.dm.get_settings(self.ids[0]).model_dump(mode="json")
        for method, options in (("GET", {}), ("PUT", {"json": settings})):
            with self.subTest(method=method):
                response = self.client.request(method, "/api/devices/unknown/settings", **options)
                self.assertEqual(404, response.status_code)
                self.assertEqual({"detail": "Device not found"}, response.json())

    def test_invalid_settings_return_422_without_changing_device(self):
        path = f"/api/devices/{self.ids[0]}/settings"
        original = self.dm.get_settings(self.ids[0]).model_dump(mode="json")
        for settings in (
            {"wavelength": 650.0},
            {**original, "averaging": "invalid"},
            {**original, "wave_length": 650.0},
            [],
            "invalid",
        ):
            with self.subTest(settings=settings):
                response = self.client.put(path, json=settings)
                self.assertEqual(422, response.status_code, response.text)
                self.assertTrue(all(error["loc"][0] == "body" for error in response.json()["detail"]))
                self.assertEqual(original, self.dm.get_settings(self.ids[0]).model_dump(mode="json"))

    def test_device_settings_api_returns_operation_failures(self):
        path = f"/api/devices/{self.ids[0]}/settings"
        settings = self.dm.get_settings(self.ids[0]).model_dump(mode="json")
        with patch.object(self.devices[0], "get_settings", side_effect=OSError("settings read failed")):
            response = self.client.get(path)
            self.assertEqual(503, response.status_code)
            self.assertEqual({"detail": "settings read failed"}, response.json())
        with patch.object(self.devices[0], "set_settings", side_effect=OSError("settings update failed")):
            response = self.client.put(path, json=settings)
            self.assertEqual(503, response.status_code)
            self.assertEqual({"detail": "settings update failed"}, response.json())

    def test_settings_api_handles_another_devices_settings_model(self):
        class SensorSettings(BaseModel):
            enabled: bool
            offsets: list[float]

            @field_validator("offsets")
            @classmethod
            def validate_offsets(cls, value):
                if not value:
                    raise ValueError("offsets must not be empty")
                return value

        path = f"/api/devices/{self.ids[0]}/settings"
        initial = SensorSettings(enabled=True, offsets=[0.0])
        settings = {"enabled": False, "offsets": [1.5, 2.5]}
        # デバイスの設定メソッドを別のモデルへ差し替え、共通APIの入出力を確認する。
        with patch.object(self.devices[0], "get_settings", return_value=initial), patch.object(
            self.devices[0], "set_settings", side_effect=SensorSettings.model_validate,
        ):
            self.assertEqual(initial.model_dump(mode="json"), self.client.get(path).json())
            response = self.client.put(path, json=settings)
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual(settings, response.json())

            # 検証エラーにValueErrorが含まれる場合も、JSON形式の422応答を返す。
            response = self.client.put(path, json={"enabled": True, "offsets": []})
            self.assertEqual(422, response.status_code, response.text)
            self.assertEqual(["body", "offsets"], response.json()["detail"][0]["loc"])

    def test_device_api_returns_operation_failures(self):
        # 接続・スキャン・停止の失敗を、理由付きの503応答として返すことを確認する。
        device_id = self.ids[0]
        with patch.object(self.devices[0], "connect", return_value=False):
            self.assertEqual(503, self.client.post(f"/api/devices/{device_id}/connect").status_code)
        with patch.object(DummyPowerMeter, "scan", side_effect=OSError("scan failed")):
            response = self.client.post("/api/devices/scan")
            self.assertEqual(503, response.status_code)
            self.assertEqual("scan failed", response.json()["detail"])
        device, release = self.start_blocked_device()
        response = self.client.post(f"/api/devices/{device_id}/disconnect")
        self.assertEqual(503, response.status_code)
        self.assertIn("Timed out", response.json()["detail"])
        self.assertTrue(self.mm.is_sampling(device_id))
        release.set()

    def test_shutdown_lifespan_is_repeatable(self):
        # 同じアプリを再起動した場合も、各lifespanで作ったサービスを終了する。
        app = create_app()
        for _ in range(2):
            with TestClient(app):
                dm = app.state.device_manager
                mm = app.state.measurement_manager
                dm.scan()
                devices = list(dm.get_all().values())
                for device_id, device in dm.get_all().items():
                    device.connect()
                    mm.start_sampling(device_id)
            self.assertIsNone(mm._processor_thread)
            self.assertTrue(all(not device.is_sampling() for device in devices))
            self.assertTrue(all(not device.connected for device in devices))

    def test_device_shutdown_continues_after_one_device_failure(self):
        # DeviceManagerは1台の切断失敗後も、残りの機器を停止・切断する。
        device, release = self.start_blocked_device()
        self.mm.start_sampling(self.ids[1])
        with self.assertRaises(RuntimeError):
            self.dm.shutdown()
        self.assertTrue(self.mm.is_sampling(self.ids[0]))
        self.assertFalse(self.mm.is_sampling(self.ids[1]))
        self.assertFalse(self.devices[1].connected)
        self.assertEqual("error", device.get_info().status)
        release.set()

    def test_gui_entry_point_requests_server_exit_when_window_fails(self):
        # 実ウィンドウとサーバーを起動せず、GUIの例外後にも終了要求と待機を行う。
        import run

        with patch.object(run, "app", self.app), patch.object(run.uvicorn, "Server") as servers, patch.object(run.threading, "Thread") as threads, patch.object(run, "WindowManager") as windows:
            servers.return_value.should_exit = False
            windows.return_value.start.side_effect = RuntimeError("mock window closed")
            with self.assertRaisesRegex(RuntimeError, "mock window closed"):
                run.main()
        self.assertTrue(servers.return_value.should_exit)
        threads.return_value.start.assert_called_once_with()
        threads.return_value.join.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
