"""アプリ終了時の独立した後始末と、動作中の機器の保持を確認する。"""

import logging
import unittest
from unittest.mock import patch

from backend.app.main import create_app


class ApplicationShutdownTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)

    async def test_sampling_failure_keeps_live_device_but_cleans_other_services(self):
        app = create_app()
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        mm = app.state.measurement_manager
        dm = app.state.device_manager
        preview = app.state.camera_preview_service
        broadcaster = app.state.event_broadcaster
        dm.scan()
        meters = [device for device in dm.get_all().values() if device.get_info().type == "power_meter"]
        try:
            for device in meters:
                device.connect()
                mm.start_sampling(device.device_id)
            with patch.object(meters[0], "stop_sampling", side_effect=RuntimeError("sampling failure")), \
                    patch.object(preview, "shutdown", wraps=preview.shutdown) as close_preview, \
                    patch.object(broadcaster, "shutdown", wraps=broadcaster.shutdown) as close_events:
                with self.assertRaises(ExceptionGroup) as caught:
                    await context.__aexit__(None, None, None)
                close_preview.assert_called_once()
                close_events.assert_called_once()
            self.assertTrue(meters[0].is_sampling())
            self.assertTrue(meters[0].connected)
            self.assertFalse(meters[1].is_sampling())
            self.assertFalse(meters[1].connected)
            self.assertIn("measurement", str(caught.exception))
            self.assertIn(meters[0].device_id, str(caught.exception))
        finally:
            mm.shutdown()
            preview.shutdown()
            dm.shutdown()
            broadcaster.shutdown()

    async def test_preview_and_disconnect_failures_do_not_skip_other_cleanup(self):
        app = create_app()
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        mm = app.state.measurement_manager
        dm = app.state.device_manager
        preview = app.state.camera_preview_service
        broadcaster = app.state.event_broadcaster
        dm.scan()
        devices = list(dm.get_all().values())
        try:
            for device in devices:
                device.connect()
            with patch.object(preview, "shutdown", side_effect=RuntimeError("preview cleanup failure")), \
                    patch.object(devices[0], "disconnect", side_effect=OSError("disconnect failure")), \
                    patch.object(broadcaster, "shutdown", wraps=broadcaster.shutdown) as close_events:
                with self.assertRaises(ExceptionGroup) as caught:
                    await context.__aexit__(None, None, None)
                close_events.assert_called_once()
            self.assertTrue(all(not device.connected for device in devices[1:]))
            self.assertIn("preview cleanup failure", str(caught.exception))
            self.assertIn("disconnect failure", str(caught.exception))
            self.assertIsNone(mm._processor_thread)
        finally:
            mm.shutdown()
            preview.shutdown()
            dm.shutdown()
            broadcaster.shutdown()


if __name__ == "__main__":
    unittest.main()
