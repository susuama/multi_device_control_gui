"""測定期間の確定、取得停止後の保持、状態APIの参照動作を確認する。"""

import logging
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.measurements import router
from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.main import create_app
from backend.app.models.measurement import DeviceData
from backend.app.services.device_manager import DeviceManager
from backend.app.services.event_broadcaster import EventBroadcaster
from backend.app.services.measurement_manager import MeasurementManager
from backend.app.utils.time_utils import get_counter_ns


class MeasurementStatusTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.dm = DeviceManager([DummyPowerMeter])
        self.events = EventBroadcaster()
        self.mm = MeasurementManager(self.dm, self.events, server_instance_id="test-server")
        self.addCleanup(self.dm.shutdown)
        self.addCleanup(self.mm.shutdown)
        self.dm.scan()
        self.ids = list(self.dm.get_all())
        for device_id in self.ids:
            self.dm.connect(device_id)
        app = FastAPI()
        app.state.measurement_manager = self.mm
        app.include_router(router)
        self.client = self.enterContext(TestClient(app))

    def status(self):
        response = self.client.get("/api/measurements/status")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_idle_get_has_no_device_operation_or_fabricated_measurement(self):
        with patch.object(self.dm, "scan", side_effect=AssertionError("scan")), \
                patch.object(self.mm, "start_sampling", side_effect=AssertionError("start")):
            first = self.status()
            self.assertEqual(first, self.status())
        self.assertEqual({
            "server_instance_id": "test-server", "measurement_id": None,
            "started_at": None, "sampling_device_ids": [], "error": None,
        }, first)

    def test_additional_devices_share_id_and_stop_keeps_last_period(self):
        self.mm.start_sampling(self.ids[0])
        first = self.status()
        self.assertTrue(first["measurement_id"])
        self.assertEqual(self.mm._start_datetime.isoformat(), first["started_at"])
        self.assertEqual(self.mm._start_counter_ns, self.mm._measurement_period.start_counter_ns)
        self.mm.start_sampling(self.ids[1])
        second = self.status()
        self.assertEqual(first["measurement_id"], second["measurement_id"])
        self.assertEqual(first["started_at"], second["started_at"])
        self.assertEqual(set(self.ids), set(second["sampling_device_ids"]))
        self.mm.stop_sampling(self.ids[0])
        self.assertEqual([self.ids[1]], self.status()["sampling_device_ids"])
        self.mm.stop_all_sampling()
        stopped = self.status()
        self.assertEqual([], stopped["sampling_device_ids"])
        self.assertEqual(first["measurement_id"], stopped["measurement_id"])
        self.assertEqual(first["started_at"], stopped["started_at"])

    def test_restart_without_recording_creates_new_period(self):
        self.mm.start_sampling(self.ids[0])
        first = self.status()
        self.mm.stop_all_sampling()
        self.mm.start_sampling(self.ids[0])
        second = self.status()
        self.assertNotEqual(first["measurement_id"], second["measurement_id"])
        self.assertEqual(first["server_instance_id"], second["server_instance_id"])

    def test_restart_during_recording_preserves_period(self):
        with tempfile.TemporaryDirectory() as directory:
            self.mm.start_sampling(self.ids[0])
            first = self.status()
            self.mm.start_recording(directory, "recording")
            try:
                self.mm.stop_all_sampling()
                self.mm.start_sampling(self.ids[0])
                second = self.status()
                self.assertEqual(first["measurement_id"], second["measurement_id"])
                self.assertEqual(first["started_at"], second["started_at"])
            finally:
                self.mm.stop_recording()
            self.assertTrue((Path(directory) / "recording" / "metadata.json").is_file())

    def test_first_start_failure_keeps_empty_period(self):
        self.dm.disconnect(self.ids[0])
        with self.assertRaises(RuntimeError):
            self.mm.start_sampling(self.ids[0])
        self.assertIsNone(self.status()["measurement_id"])
        self.assertIsNone(self.status()["started_at"])

    def test_failed_additional_device_does_not_replace_period(self):
        self.mm.start_sampling(self.ids[0])
        first = self.status()
        self.dm.disconnect(self.ids[1])
        with self.assertRaises(RuntimeError):
            self.mm.start_sampling(self.ids[1])
        self.assertEqual(first, self.status())

    def test_successful_start_releases_early_data_after_period_is_committed(self):
        device = self.dm.get(self.ids[0])
        real_start = device.start_sampling
        entered = threading.Event()
        release = threading.Event()
        errors = []

        def start(output, **kwargs):
            result = real_start(output, **kwargs)
            entered.set()
            if not release.wait(2):
                raise TimeoutError("Test did not release start")
            return result

        def run():
            try:
                self.mm.start_sampling(self.ids[0])
            except Exception as exc:
                errors.append(exc)

        with patch.object(device, "start_sampling", side_effect=start), \
                patch.object(self.mm, "_get_elapsed", wraps=self.mm._get_elapsed) as elapsed:
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertIsNone(self.mm._measurement_period)
                elapsed.assert_not_called()
            finally:
                release.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.mm.stop_all_sampling()
            self.mm.data_queue.join()
            self.assertTrue(elapsed.called)
        self.assertEqual([], errors)
        self.assertTrue(self.status()["measurement_id"])
        self.assertIn(self.ids[0], self.mm.latest_settings)

    def test_immediate_read_error_still_identifies_the_successful_start(self):
        device = self.dm.get(self.ids[0])
        with patch.object(device, "_read_sample", side_effect=RuntimeError("read failed")):
            self.mm.start_sampling(self.ids[0])
            device._sampling_thread.join(1)
        self.assertTrue(self.status()["measurement_id"])
        self.assertEqual([], self.status()["sampling_device_ids"])

    def test_failed_restart_keeps_last_successful_period(self):
        self.mm.start_sampling(self.ids[0])
        first = self.status()
        self.mm.stop_all_sampling()
        self.dm.disconnect(self.ids[0])
        with self.assertRaises(RuntimeError):
            self.mm.start_sampling(self.ids[0])
        self.assertEqual(first["measurement_id"], self.status()["measurement_id"])
        self.assertEqual(first["started_at"], self.status()["started_at"])
        self.assertEqual([], self.status()["sampling_device_ids"])
        self.dm.connect(self.ids[0])
        self.mm.start_sampling(self.ids[0])
        self.assertNotEqual(first["measurement_id"], self.status()["measurement_id"])

    def test_failed_start_notifications_are_discarded_without_waiting_for_state_lock(self):
        entered = threading.Event()
        release = threading.Event()
        errors = []
        device = self.dm.get(self.ids[0])

        def start(output, **kwargs):
            output.put(DeviceData(self.ids[0], "settings", get_counter_ns(), {"failed": True}), timeout=0.1)
            output.put(DeviceData(self.ids[0], "sample", get_counter_ns(), {"power_w": 1}), timeout=0.1)
            entered.set()
            if not release.wait(2):
                raise TimeoutError("Test did not release start")
            raise RuntimeError("start failed after notifications")

        def run():
            try:
                self.mm.start_sampling(self.ids[0])
            except Exception as exc:
                errors.append(exc)

        with patch.object(device, "start_sampling", side_effect=start), \
                patch.object(self.mm, "_get_elapsed", wraps=self.mm._get_elapsed) as elapsed:
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertIsNone(self.mm._measurement_period)
            finally:
                release.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.mm.data_queue.join()
            elapsed.assert_not_called()
        self.assertEqual(1, len(errors))
        self.assertNotIn(self.ids[0], self.mm.latest_settings)
        self.assertIsNone(self.status()["measurement_id"])

    def test_queue_error_and_stop_failure_are_visible(self):
        self.mm.start_sampling(self.ids[0])
        stopped = threading.Event()

        def fail():
            stopped.set()
            raise RuntimeError("stop rejected")

        with patch.object(self.mm, "stop_all_sampling", side_effect=fail):
            self.mm._request_queue_stop(reason="queue_threshold_exceeded", queue_size=301)
            self.assertTrue(stopped.wait(1))
            deadline = time.monotonic() + 1
            while "Stop failed" not in (self.status()["error"] or "") and time.monotonic() < deadline:
                time.sleep(0.005)
        error = self.status()["error"]
        self.assertIn("301", error)
        self.assertIn("Stop failed", error)
        self.assertEqual(error, self.mm.get_recording_status().error)
        self.mm.stop_all_sampling()
        self.mm.start_sampling(self.ids[0])
        self.assertIsNone(self.status()["error"])

    def test_api_failure_is_503(self):
        with patch.object(self.mm, "get_measurement_status", side_effect=RuntimeError("status failed")):
            response = self.client.get("/api/measurements/status")
        self.assertEqual(503, response.status_code)
        self.assertEqual("status failed", response.json()["detail"])

    def test_shutdown_retains_last_measurement(self):
        self.mm.start_sampling(self.ids[0])
        first = self.status()
        self.mm.shutdown()
        self.assertEqual(first["measurement_id"], self.status()["measurement_id"])
        self.assertEqual([], self.status()["sampling_device_ids"])


class MeasurementStatusLifespanTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)

    def test_service_restart_changes_server_id_and_http_reconnect_does_not(self):
        app = create_app()
        with TestClient(app) as client:
            first = client.get("/api/measurements/status").json()
            reconnect = TestClient(app)
            try:
                second = reconnect.get("/api/measurements/status").json()
            finally:
                reconnect.close()
            self.assertEqual(first["server_instance_id"], second["server_instance_id"])
        with TestClient(app) as client:
            restarted = client.get("/api/measurements/status").json()
        self.assertNotEqual(first["server_instance_id"], restarted["server_instance_id"])
