"""実機や製品バックエンドを使わず、中核API契約を再現する。"""

import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image


class MockApi:
    """単一イベントループ上で、取得と記録の模擬状態を保持する。"""

    def __init__(self) -> None:
        self.server_id = uuid.uuid4().hex
        self.measurement_id: str | None = None
        self.started_at: str | None = None
        self.start_ns = 0
        self.offline = False
        self.invalid_start_response = False
        self.stale_context: tuple[str, str] | None = None
        self.stale_epoch = 0
        self.active: set[str] = set()
        self.connections = 0
        self.requests: Counter = Counter()
        self.sent: Counter = Counter()
        self.devices = {name: {
            "id": name, "type": "camera" if name == "camera" else "power_meter",
            "manufacturer": "Mock", "model": "CoreTest", "serial_number": name,
            "address": "mock", "status": "disconnected",
        } for name in ["camera", "meter-1", "meter-2"]}
        self.settings = {name: ({
            "width": 640, "height": 480, "fps": 20, "exposure": 0.01, "gain": 1,
            "gain_boost": False, "pixel_format": "mono8", "roi": None,
        } if name == "camera" else {
            "wavelength": 500, "range": 1, "auto_range": True, "bandwidth": "low", "averaging": 1,
        }) for name in self.devices}
        self.recording = {"server_instance_id": self.server_id, "recording_id": None,
            "measurement_id": None, "is_recording": False, "is_stopping": False,
            "directory": None, "name": None, "error": None, "targets": {}}
        image = BytesIO()
        Image.new("L", (640, 480), 110).save(image, format="JPEG")
        self.jpeg = image.getvalue()

    def new_measurement(self) -> None:
        """共通時刻を更新し、直前の期間を遅延配信の試験用に残す。"""
        if self.measurement_id:
            self.stale_context = (self.server_id, self.measurement_id)
        self.measurement_id = uuid.uuid4().hex
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.start_ns = time.perf_counter_ns()

    def measurement_status(self) -> dict:
        """現在の共通測定基準と取得対象を返す。"""
        return {"server_instance_id": self.server_id, "measurement_id": self.measurement_id,
            "started_at": self.started_at, "sampling_device_ids": sorted(self.active), "error": None}

    def metrics(self) -> dict:
        """要求・配信の件数を返す。イベント本文は蓄積しない。"""
        return {"requests": dict(self.requests), "sent": dict(self.sent), "connections": self.connections,
            "active": sorted(self.active), "measurement_id": self.measurement_id,
            "jpeg_bytes": len(self.jpeg), "camera_fps": 20, "meter_hz": 10}


def create_mock_app(build_dir: Path) -> FastAPI:
    """検証画面と模擬APIだけを配信するアプリを作成する。

    Args:
        build_dir (Path): 検証専用画面のビルド先。

    Returns:
        FastAPI: 製品バックエンドに依存しない検証サーバー。
    """
    app = FastAPI()
    model = MockApi()

    @app.middleware("http")
    async def track(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            model.requests[f"{request.method} {request.url.path}"] += 1
            if model.offline:
                return JSONResponse({"detail": "試験用の通信断"}, status_code=503)
        return await call_next(request)

    @app.get("/_test/metrics")
    async def metrics():
        return model.metrics()

    @app.post("/_test/control")
    async def control(request: Request):
        command = await request.json()
        if "offline" in command:
            model.offline = bool(command["offline"])
        if command.get("new_measurement"):
            model.new_measurement()
        if command.get("emit_old"):
            model.stale_epoch += 1
        if command.get("invalid_start_response"):
            model.invalid_start_response = True
        return model.metrics()

    @app.get("/api/status")
    async def status():
        return {"status": "ok"}

    @app.get("/api/devices")
    @app.post("/api/devices/scan")
    async def devices():
        return list(model.devices.values())

    @app.get("/api/measurements/status")
    async def measurement():
        return model.measurement_status()

    @app.get("/api/recordings/status")
    async def recording_status():
        return model.recording

    @app.post("/api/recordings/start")
    async def recording_start(request: Request):
        body = await request.json()
        if not model.active or model.recording["is_recording"] or model.recording["is_stopping"]:
            raise HTTPException(409, "記録を開始できません。")
        await asyncio.sleep(0.15)
        model.recording = {"server_instance_id": model.server_id, "recording_id": uuid.uuid4().hex,
            "measurement_id": model.measurement_id, "is_recording": True, "is_stopping": False,
            "directory": body["directory"], "name": body.get("name"), "error": None,
            "targets": {name: {"type": model.devices[name]["type"], "settings_at_start": deepcopy(model.settings[name]),
                "video_options": body.get("video_options", {}).get(name) if name == "camera" else None,
                "state": "recording", "error": None, "locked_settings": ["fps", "roi"] if name == "camera" else []}
                for name in sorted(model.active)}}
        if model.invalid_start_response:
            model.invalid_start_response = False
            return {"is_recording": True}
        return model.recording

    @app.post("/api/recordings/stop")
    async def recording_stop():
        model.recording.update(is_recording=False, is_stopping=True)
        for target in model.recording["targets"].values():
            target.update(state="stopping", locked_settings=[])
        await asyncio.sleep(1.4)
        for target in model.recording["targets"].values():
            target["state"] = "completed"
        model.recording["is_stopping"] = False
        return model.recording

    @app.api_route("/api/devices/{device_id}/{operation:path}", methods=["GET", "POST", "PUT"])
    async def device_operation(device_id: str, operation: str, request: Request):
        if device_id not in model.devices:
            raise HTTPException(404, "機器がありません。")
        device = model.devices[device_id]
        if operation == "settings":
            if request.method == "PUT":
                settings = await request.json()
                target = model.recording["targets"].get(device_id, {})
                for key in target.get("locked_settings", []):
                    if settings[key] != model.settings[device_id][key]:
                        raise HTTPException(409, f"設定 {key} は固定されています。")
                model.settings[device_id] = settings
            return model.settings[device_id]
        if operation == "sampling":
            return {"is_sampling": device_id in model.active}
        if operation in ["connect", "disconnect", "sampling/start", "sampling/stop"]:
            await asyncio.sleep(0.1)
            if operation == "connect":
                device["status"] = "connected"
            elif operation == "disconnect":
                device["status"] = "disconnected"
                model.active.discard(device_id)
            elif operation == "sampling/start":
                if device["status"] != "connected":
                    raise HTTPException(409, "未接続です。")
                if not model.active and not model.recording["is_recording"]:
                    model.new_measurement()
                model.active.add(device_id)
            else:
                model.active.discard(device_id)
            return {"success": True}
        raise HTTPException(404, "未対応の検証操作です。")

    @app.get("/api/devices/{device_id}")
    async def device_info(device_id: str):
        if device_id not in model.devices:
            raise HTTPException(404, "機器がありません。")
        return model.devices[device_id]

    async def stream(websocket: WebSocket, key: str | None):
        await websocket.accept()
        model.connections += 1

        async def transmit():
            frame = 0
            stale_epoch = model.stale_epoch
            while not model.offline:
                if model.measurement_id:
                    names = [key] if key is not None and key in model.active else (
                        [name for name in sorted(model.active) if name != "camera"] if key is None else [])
                    stale = stale_epoch != model.stale_epoch and model.stale_context is not None
                    context = model.stale_context if stale else (model.server_id, model.measurement_id)
                    stale_epoch = model.stale_epoch
                    now = time.perf_counter_ns()
                    for name in names:
                        event = {"server_instance_id": context[0], "measurement_id": context[1], "id": name}
                        if key is None:
                            event.update(type="measurement", timestamp_ns=now, elapsed_s=(now - model.start_ns) / 1e9,
                                datetime=model.started_at, data={"power_w": frame / 1000})
                            await websocket.send_json(event)
                        else:
                            event.update(type="camera_preview", frame_index=frame, timestamp_ns=str(now), width=640, height=480)
                            header = json.dumps(event, separators=(",", ":")).encode("utf-8")
                            await websocket.send_bytes(len(header).to_bytes(4, "big") + header + model.jpeg)
                        model.sent[name] += 1
                    frame += 1
                await asyncio.sleep(0.1 if key is None else 0.05)
            await websocket.close(1013, "試験用の通信断")

        async def receive_close():
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass

        tasks = [asyncio.create_task(transmit()), asyncio.create_task(receive_close())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            model.connections -= 1

    @app.websocket("/ws")
    async def all_events(websocket: WebSocket):
        await stream(websocket, None)

    @app.websocket("/ws/{key}")
    async def keyed_events(websocket: WebSocket, key: str):
        await stream(websocket, key)

    app.mount("/", StaticFiles(directory=build_dir, html=True), name="fixture")
    return app
