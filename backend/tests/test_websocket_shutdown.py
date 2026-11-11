"""切断競合の再現と、実サーバー終了時の取得・記録の後始末を確認する。"""

import asyncio
import csv
import json
import logging
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

import uvicorn
from uvicorn.protocols.websockets.websockets_sansio_impl import WebSocketsSansIOProtocol
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidState
from websockets.frames import Frame, Opcode
from websockets.protocol import OPEN, CLOSING, CLOSED
from websockets.server import ServerProtocol

from backend.app.main import create_app
from desktop.websocket_protocol import ShutdownSafeWebSocketProtocol


def protocol_for(cls, state=OPEN):
    """ソケットを作らず、実websocketsの状態機械と終了処理を組み合わせる。"""
    protocol = cls.__new__(cls)
    protocol.conn = ServerProtocol(state=state)
    protocol.close_sent = False
    protocol.close_timer = Mock()
    protocol.handshake_complete = True
    protocol.queue = asyncio.Queue()
    protocol.transport = Mock()
    protocol.stop_keepalive = Mock()
    return protocol


class WebSocketShutdownTests(unittest.TestCase):
    def test_peer_close_reproduces_original_failure_and_fixed_shutdown_finishes(self):
        for cls in (WebSocketsSansIOProtocol, ShutdownSafeWebSocketProtocol):
            with self.subTest(protocol=cls.__name__):
                protocol = protocol_for(cls)
                protocol.conn.receive_data(Frame(Opcode.CLOSE, b"\x03\xe8").serialize(mask=True))
                self.assertEqual(protocol.conn.state, CLOSING)
                if cls is WebSocketsSansIOProtocol:
                    with self.assertRaisesRegex(InvalidState, "connection is closing"):
                        protocol.shutdown()
                else:
                    timer = protocol.close_timer
                    protocol.shutdown()
                    protocol.shutdown()
                    protocol.transport.close.assert_called()
                    timer.cancel.assert_called_once()
                    self.assertIsNone(protocol.close_timer)

    def test_open_connection_still_sends_normal_server_close(self):
        protocol = protocol_for(ShutdownSafeWebSocketProtocol)
        protocol.shutdown()
        self.assertEqual(protocol.conn.close_sent.code, 1012)
        self.assertEqual(protocol.queue.get_nowait(), {"type": "websocket.disconnect", "code": 1012})
        protocol.transport.write.assert_called_once()

    def test_closed_connection_does_not_send_another_close(self):
        protocol = protocol_for(ShutdownSafeWebSocketProtocol, CLOSED)
        protocol.shutdown()
        protocol.transport.write.assert_not_called()
        protocol.transport.close.assert_called_once()

    def test_unexpected_shutdown_error_is_not_hidden(self):
        protocol = protocol_for(ShutdownSafeWebSocketProtocol)
        protocol.conn.send_close = Mock(side_effect=RuntimeError("unexpected failure"))
        with self.assertRaisesRegex(RuntimeError, "unexpected failure"):
            protocol.shutdown()

    def test_desktop_entry_point_selects_fixed_protocol(self):
        import run
        with patch.object(run.uvicorn, "Config") as config, patch.object(run.uvicorn, "Server"), \
                patch.object(run.threading, "Thread"), patch.object(run, "WindowManager"):
            run.main()
        self.assertIs(config.call_args.kwargs["ws"], ShutdownSafeWebSocketProtocol)


class LiveWebSocketShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_sampling_and_recording_close_with_client_and_server_shutdown(self):
        previous = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, previous)
        for recording in (False, True):
            with self.subTest(recording=recording), tempfile.TemporaryDirectory() as directory:
                app = create_app()
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                port = listener.getsockname()[1]
                server = uvicorn.Server(uvicorn.Config(app, ws=ShutdownSafeWebSocketProtocol, log_config=None, timeout_graceful_shutdown=5))
                serve = asyncio.create_task(server.serve(sockets=[listener]))
                client = None
                try:
                    async with asyncio.timeout(10):
                        while not server.started:
                            if serve.done():
                                await serve
                            await asyncio.sleep(0.01)
                    dm = app.state.device_manager
                    mm = app.state.measurement_manager
                    dm.scan()
                    device = next(device for device in dm.get_all().values() if device.get_info().type == "power_meter")
                    device_id = device.device_id
                    device.connect()
                    client = await connect(f"ws://127.0.0.1:{port}/ws")
                    await asyncio.to_thread(mm.start_sampling, device_id)
                    await asyncio.wait_for(client.recv(), timeout=5)
                    if recording:
                        await asyncio.to_thread(mm.start_recording, directory, "shutdown-test")
                        for _ in range(3):
                            await asyncio.wait_for(client.recv(), timeout=5)
                    close = asyncio.create_task(client.close())
                    server.should_exit = True
                    await asyncio.wait_for(serve, timeout=15)
                    await asyncio.wait_for(close, timeout=5)
                    self.assertFalse(device.is_sampling())
                    self.assertFalse(device.connected)
                    self.assertIsNone(mm._processor_thread)
                    self.assertFalse(mm._data_recorder.has_pending_cleanup())
                    if recording:
                        folder = Path(directory) / "shutdown-test"
                        metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
                        self.assertIsNotNone(metadata["recording_stopped_at"])
                        with next(folder.glob("*.csv")).open(encoding="utf-8-sig", newline="") as file:
                            rows = list(csv.DictReader(file))
                        self.assertTrue(rows)
                        self.assertIn("power_w", rows[0])
                finally:
                    server.should_exit = True
                    if client is not None:
                        await client.close()
                    if not serve.done():
                        await asyncio.wait_for(serve, timeout=15)
                    listener.close()


if __name__ == "__main__":
    unittest.main()
