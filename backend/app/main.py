"""
FastAPIとサービス群を組み立てる

サーバー起動は run.py から別スレッドで実行される。

FastAPIのエンドポイントからウィンドウを操作するために、
WindowManagerインスタンスを FastAPI インスタンスに保持する。

URLルーティングは別ファイル（app/api/*.py）にまとめる。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
import asyncio

# FastAPIのルーティング
from backend.app.api.status  import router as status_router
from backend.app.api.devices import router as device_router
from backend.app.api.windows import router as window_router
from backend.app.api.recordings import router as recording_router
from backend.app.api.events  import router as events_router

# デバイスの管理を行うサービス群
from backend.app.services.device_manager import DeviceManager
from backend.app.services.measurement_manager import MeasurementManager
from backend.app.services.event_broadcaster import EventBroadcaster
from backend.app.services.camera_preview import CameraPreviewService

# 探索対象のデバイスドライバーの一覧
from backend.app.devices.registry import DEVICE_CLASSES


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPIの起動時・終了時の処理を行う。
    """
    # ========================== 起動処理 ==========================
    
    # デバイスの探索と管理を行うインスタンスを初期化
    device_manager         = DeviceManager(device_classes=DEVICE_CLASSES)
    event_broadcaster      = EventBroadcaster()
    event_broadcaster.start(asyncio.get_running_loop())
    camera_preview_service = CameraPreviewService(event_broadcaster)
    measurement_manager    = MeasurementManager(
        device_manager=device_manager, 
        event_broadcaster=event_broadcaster,
        camera_preview_service=camera_preview_service,
        )
    app.state.device_manager      = device_manager
    app.state.measurement_manager = measurement_manager
    app.state.event_broadcaster   = event_broadcaster
    app.state.camera_preview_service = camera_preview_service

    try:
        yield

    finally:
        # ========================== 終了処理 ==========================

        # 1. 全デバイスのsamplingを停止
        # 2. Queueに残っているデータを処理
        # 3. Processor Threadを停止
        errors = []
        try:
            measurement_manager.shutdown()
        except Exception as exc:
            errors.append(("measurement", exc))

        # 測定側に未完了があっても、独立したサービスの終了をそれぞれ試みる。
        try:
            camera_preview_service.shutdown()
        except Exception as exc:
            errors.append(("camera preview", exc))

        for device_id, device in device_manager.get_all().items():
            try:
                if device.is_sampling():
                    raise RuntimeError(f"Device is still sampling: {device_id}")
                if not device_manager.disconnect(device_id):
                    raise RuntimeError(f"Could not disconnect device: {device_id}")
            except Exception as exc:
                errors.append((f"device {device_id}", exc))

        try:
            event_broadcaster.shutdown()
        except Exception as exc:
            errors.append(("event broadcaster", exc))

        for stage, error in errors:
            error.add_note(f"Application shutdown stage: {stage}")
        if len(errors) == 1:
            raise errors[0][1]
        if errors:
            message = "; ".join(f"{stage}: {error}" for stage, error in errors)
            raise ExceptionGroup(f"Application shutdown failed: {message}", [error for _, error in errors])


def create_app() -> FastAPI:
    """
    FastAPIのインスタンスを作成する。

    Returns:
        return (FastAPI): FastAPIインスタンス。
    """
    app = FastAPI(title="Research App API", lifespan=lifespan)

    # WindowManagerインスタンスを保持するための属性
    app.state.window_manager = None

    # APIの登録
    app.include_router(status_router)
    app.include_router(window_router)
    app.include_router(device_router)
    app.include_router(recording_router)
    app.include_router(events_router)

    return app


app = create_app()
