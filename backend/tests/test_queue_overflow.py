"""共通Queueの件数監視、満杯通知、記録継続とスレッド終了を検証する。"""

import csv
import json
import logging
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api.devices import router as devices_router
from backend.app.api.recordings import router as recordings_router
from backend.app.devices.camera.dummy_camera import DummyCamera
from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.models.measurement import DeviceData
from backend.app.services.device_manager import DeviceManager
from backend.app.services.measurement_manager import MeasurementManager
from backend.app.utils.time_utils import get_counter_ns


class QueueOverflowTests(unittest.TestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="queue-overflow-")))
        self.dm = DeviceManager([DummyPowerMeter])
        self.events = Mock()
        self.events.is_running.return_value = False
        self.mm = MeasurementManager(self.dm, self.events)
        self.dm.scan()
        self.ids = list(self.dm.get_all())
        for device in self.dm.get_all().values():
            device.connect()
        self.releases = []
        self.workers = []
        self.addCleanup(self.shutdown)
        app = FastAPI()
        app.state.device_manager = self.dm
        app.state.measurement_manager = self.mm
        app.include_router(devices_router)
        app.include_router(recordings_router)
        self.client = self.enterContext(TestClient(app))

    def shutdown(self):
        for release in self.releases:
            release.set()
        for worker in self.workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.mm.shutdown()
        self.dm.shutdown()
        self.assertIsNone(self.mm._queue_monitor_thread)
        self.assertIsNone(self.mm._processor_thread)

    def wait_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(predicate())

    def start_devices(self):
        for device_id in self.ids:
            self.mm.start_sampling(device_id)
        self.mm.data_queue.join()

    def block_processor(self):
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        real_elapsed = self.mm._get_elapsed

        def elapsed(timestamp):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release Processor.")
            return real_elapsed(timestamp)

        self.enterContext(patch.object(self.mm, "_get_elapsed", side_effect=elapsed))
        self.mm.data_queue.put(self.sample(0))
        self.assertTrue(entered.wait(1))
        return release

    def sample(self, index):
        return DeviceData(self.ids[0], "sample", get_counter_ns(), {"power_w": float(index)})

    def fill_queue(self):
        while True:
            try:
                self.mm.data_queue.put_nowait(self.sample(0))
            except queue.Full:
                return

    def test_monitor_stops_all_devices_while_processor_is_blocked(self):
        self.start_devices()
        release = self.block_processor()
        self.assertEqual(350, self.mm.data_queue.maxsize)
        with patch.object(self.mm, "stop_all_sampling", wraps=self.mm.stop_all_sampling) as stop:
            for index in range(301):
                self.mm.data_queue.put_nowait(self.sample(index))
            self.wait_until(lambda: self.mm._queue_stop_attempted)
            self.assertFalse(any(device.is_sampling() for device in self.dm.get_all().values()))
            self.assertEqual(1, stop.call_count)
            time.sleep(0.15)
            self.assertEqual(1, stop.call_count)
        status = self.client.get("/api/recordings/status")
        self.assertEqual(200, status.status_code)
        self.assertIn("exceeded 300", status.json()["error"])
        self.assertIsNone(status.json()["directory"])
        self.assertEqual([], list(self.directory.iterdir()))
        self.assertTrue(self.mm._processor_thread.is_alive())
        self.assertTrue(all(device.connected and device.last_error is None for device in self.dm.get_all().values()))
        release.set()
        self.mm.data_queue.join()
        self.mm.start_sampling(self.ids[0])
        self.assertIsNone(self.mm.get_recording_status().error)
        self.mm._request_queue_stop(self.ids[0])
        self.wait_until(lambda: self.mm._queue_stop_attempted)
        self.assertFalse(self.dm.get(self.ids[0]).is_sampling())

    def test_300_items_do_not_trigger_stop_but_301_items_do(self):
        self.start_devices()
        self.mm.stop_all_sampling()
        release = self.block_processor()
        for index in range(300):
            self.mm.data_queue.put_nowait(self.sample(index))
        time.sleep(0.25)
        self.assertIsNone(self.mm.get_recording_status().error)
        self.mm.data_queue.put_nowait(self.sample(300))
        self.wait_until(lambda: self.mm._queue_stop_attempted)
        self.assertIn("exceeded 300", self.mm.get_recording_status().error)
        release.set()

    def test_recording_remains_open_and_drains_all_admitted_samples(self):
        self.start_devices()
        self.mm.start_recording(str(self.directory), "run")
        release = self.block_processor()
        for index in range(301):
            self.mm.data_queue.put_nowait(self.sample(1000 + index))
        self.wait_until(lambda: self.mm._queue_stop_attempted)
        response = self.client.get("/api/recordings/status")
        self.assertTrue(response.json()["is_recording"])
        self.assertFalse(response.json()["is_stopping"])
        self.assertIn("exceeded 300", response.json()["error"])
        self.assertTrue(all(not file.closed for file in self.mm._data_recorder._files.values()))
        release.set()
        self.mm.data_queue.join()
        self.wait_until(lambda: bool(self.mm.get_metadata().acquisition_errors))
        self.mm.start_sampling(self.ids[0])
        self.assertIn("exceeded 300", self.mm.get_recording_status().error)
        settings = self.mm.get_settings(self.ids[0]).model_dump() | {"wavelength": 650.0}
        self.mm.set_settings(self.ids[0], settings)
        self.mm.stop_all_sampling()
        self.mm.data_queue.join()
        result = self.client.post("/api/recordings/stop")
        self.assertEqual(200, result.status_code, result.text)
        saved = json.loads((self.directory / "run" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(saved["acquisition_errors"]))
        self.assertEqual("queue_threshold_exceeded", saved["acquisition_errors"][0]["reason"])
        self.assertIsNotNone(saved["recording_stopped_at"])
        self.assertEqual(650.0, saved["devices"][self.ids[0]][-1]["settings"]["wavelength"])
        with (self.directory / "run" / f"{self.ids[0]}.csv").open(encoding="utf-8-sig", newline="") as file:
            values = {float(row["power_w"]) for row in csv.DictReader(file)}
        self.assertTrue(set(map(float, range(1000, 1301))) <= values)
        self.mm.start_sampling(self.ids[0])
        self.mm.start_recording(str(self.directory), "next")
        self.assertIsNone(self.mm.get_recording_status().error)
        self.assertEqual([], self.mm.get_metadata().acquisition_errors)

    def test_full_notification_wakes_monitor_without_waiting_for_periodic_check(self):
        self.start_devices()
        self.mm.QUEUE_STOP_THRESHOLD = 1000
        release = self.block_processor()
        self.fill_queue()
        self.wait_until(lambda: self.mm._queue_stop_attempted)
        status = self.client.get("/api/recordings/status").json()
        self.assertIn("queue is full", status["error"])
        self.assertIn(self.mm._last_queue_error.details["device_id"], self.ids)
        self.assertFalse(any(device.is_sampling() for device in self.dm.get_all().values()))
        self.assertTrue(all(device.connected and device.last_error is None for device in self.dm.get_all().values()))
        release.set()

    def test_status_does_not_wait_for_device_stop_and_notification_avoids_state_lock(self):
        self.start_devices()
        device = self.dm.get(self.ids[0])
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        real_stop = device.stop_sampling

        def stop():
            entered.set()
            release.wait(3)
            real_stop()

        self.enterContext(patch.object(device, "stop_sampling", side_effect=stop))
        self.mm._request_queue_stop(self.ids[0])
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.mm._queue_stop_requested)
        self.assertFalse(self.mm._queue_stop_attempted)
        responses = []
        worker = threading.Thread(target=lambda: responses.append(self.client.get("/api/recordings/status")))
        self.workers.append(worker)
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertIn("queue is full", responses[0].json()["error"])
        notified = threading.Event()
        worker = threading.Thread(target=lambda: (self.mm._request_queue_stop(self.ids[1]), notified.set()))
        self.workers.append(worker)
        worker.start()
        self.assertTrue(notified.wait(0.5))
        release.set()

    def test_full_command_queue_returns_503_and_never_executes_rejected_stop(self):
        self.start_devices()
        self.mm.start_recording(str(self.directory), "run")
        self.mm.QUEUE_STOP_THRESHOLD = 1000
        release = self.block_processor()
        self.fill_queue()
        self.mm.COMMAND_PUT_TIMEOUT = 0.02
        response = self.client.post("/api/recordings/stop")
        self.assertEqual(503, response.status_code)
        self.assertIn("command was not accepted", response.json()["detail"])
        self.assertTrue(self.mm.get_recording_status().is_recording)
        release.set()
        self.mm.data_queue.join()
        self.assertTrue(self.mm.get_recording_status().is_recording)

    def test_idle_overflow_does_not_rewrite_completed_metadata(self):
        self.start_devices()
        self.mm.start_recording(str(self.directory), "completed")
        self.mm.stop_recording()
        path = self.directory / "completed" / "metadata.json"
        original = path.read_bytes()
        self.mm._request_queue_stop(self.ids[0])
        self.wait_until(lambda: self.mm._queue_stop_attempted)
        self.mm.data_queue.join()
        self.assertEqual(original, path.read_bytes())
        self.assertIn("queue is full", self.mm.get_recording_status().error)

    def test_restart_keeps_all_pending_errors_until_processor_can_save_them(self):
        self.start_devices()
        self.mm.start_recording(str(self.directory), "run")
        self.mm.stop_all_sampling()
        self.mm.data_queue.join()
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        real_save = self.mm._save_queue_error_metadata

        def save():
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test did not release queue error metadata saving.")
            real_save()

        self.enterContext(patch.object(self.mm, "_save_queue_error_metadata", side_effect=save))
        self.assertTrue(entered.wait(1))
        self.mm._request_queue_stop(self.ids[0])
        self.wait_until(lambda: self.mm._queue_stop_attempted)
        self.mm.start_sampling(self.ids[0])
        self.assertFalse(self.mm._queue_stop_requested)
        self.assertFalse(self.mm._queue_stop_attempted)
        self.assertIn(self.ids[0], self.mm.get_recording_status().error)
        self.mm._request_queue_stop(self.ids[1])
        self.wait_until(lambda: self.mm._queue_stop_attempted)
        self.assertEqual([], self.mm.get_metadata().acquisition_errors)
        release.set()
        self.mm.data_queue.join()
        self.mm.stop_recording()
        saved = json.loads((self.directory / "run" / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(self.ids, [error["device_id"] for error in saved["acquisition_errors"]])
        self.assertIn(self.ids[1], self.mm.get_recording_status().error)

    def test_stop_failure_is_displayed_without_changing_detected_error_metadata(self):
        self.start_devices()
        self.mm.start_recording(str(self.directory), "run")
        with patch.object(self.mm, "stop_all_sampling", side_effect=RuntimeError("test stop failure")):
            self.mm._request_queue_stop(self.ids[0])
            self.wait_until(lambda: self.mm._queue_stop_attempted)
            status = self.client.get("/api/recordings/status").json()
            self.assertIn("queue is full", status["error"])
            self.assertIn("Stop failed: test stop failure", status["error"])
            self.assertTrue(status["is_recording"])
            self.wait_until(lambda: bool(self.mm.get_metadata().acquisition_errors))
        self.mm.stop_all_sampling()
        self.mm.stop_recording()
        saved = json.loads((self.directory / "run" / "metadata.json").read_text(encoding="utf-8"))
        error = saved["acquisition_errors"][0]
        self.assertEqual({"reason", "device_id", "queue_size", "detected_at", "message"}, set(error))
        self.assertNotIn("Stop failed", error["message"])

    def test_stop_timeout_preserves_worker_and_rejects_restart_until_it_exits(self):
        device = self.dm.get(self.ids[0])
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        real_read = device.read

        def read():
            entered.set()
            release.wait(3)
            return real_read()

        self.enterContext(patch.object(device, "read", side_effect=read))
        device.STOP_TIMEOUT = 0.02
        self.mm.start_sampling(self.ids[0])
        self.assertTrue(entered.wait(1))
        thread = device._sampling_thread
        self.mm._request_queue_stop(self.ids[0])
        self.wait_until(lambda: self.mm._queue_stop_attempted)
        self.assertIn("Stop failed", self.mm.get_recording_status().error)
        self.assertIs(thread, device._sampling_thread)
        self.assertTrue(thread.is_alive())
        self.assertTrue(self.mm._queue_monitor_thread.is_alive())
        with self.assertRaisesRegex(RuntimeError, "Stop all sampling"):
            self.mm.start_sampling(self.ids[1])
        release.set()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.mm.start_sampling(self.ids[1])


class DeviceQueueFullTests(unittest.TestCase):
    def test_initial_settings_full_notifies_and_does_not_start_a_worker(self):
        for driver in (DummyCamera, DummyPowerMeter):
            with self.subTest(driver=driver.__name__):
                device = driver("test")
                device.connect()
                output = queue.Queue(maxsize=1)
                output.put_nowait(object())
                device.SETTINGS_PUT_TIMEOUT = 0.01
                notices = []
                with self.assertRaisesRegex(queue.Full, "settings notification was not queued"):
                    device.start_sampling(output, on_queue_full=notices.append)
                self.assertEqual(["test"], notices)
                self.assertIsNone(device._sampling_thread)
                self.assertIsNone(device.data_queue)
                self.assertIsNone(device._on_queue_full)
                self.assertTrue(device.connected)
                device.disconnect()

    def test_both_drivers_notify_full_without_becoming_disconnected(self):
        for driver in (DummyCamera, DummyPowerMeter):
            with self.subTest(driver=driver.__name__):
                device = driver("test")
                device.connect()
                output = queue.Queue(maxsize=1)
                notices = []
                thread = device.start_sampling(output, on_queue_full=notices.append)
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertEqual(["test"], notices)
                self.assertTrue(device.connected)
                self.assertIsNone(device.last_error)
                self.assertEqual("settings", output.get_nowait().type)
                device.stop_sampling()
                self.assertIsNone(device._on_queue_full)
                device.disconnect()

    def test_applied_settings_are_preserved_when_notification_cannot_be_queued(self):
        for driver in (DummyCamera, DummyPowerMeter):
            with self.subTest(driver=driver.__name__):
                device = driver("test")
                device.connect()
                notices = []
                device.data_queue = queue.Queue(maxsize=1)
                device.data_queue.put_nowait(object())
                device._on_queue_full = notices.append
                device.SETTINGS_PUT_TIMEOUT = 0.01
                changes = {"gain": 2.0} if driver is DummyCamera else {"wavelength": 650.0}
                settings = device.get_settings().model_dump() | changes
                with self.assertRaisesRegex(queue.Full, "Settings were applied"):
                    device.set_settings(settings)
                self.assertEqual(settings, device.get_settings().model_dump())
                self.assertEqual(["test"], notices)
                self.assertTrue(device._stop_event.is_set())
                self.assertTrue(device.connected)
                device.disconnect()


if __name__ == "__main__":
    unittest.main()
