"""
FastAPIとサービス群を組み立てる

サーバー起動は run.py から別スレッドで実行される。

FastAPIのエンドポイントからウィンドウを操作するために、
WindowManagerインスタンスを FastAPI インスタンスに保持する。

URLルーティングは別ファイル（app/api/*.py）にまとめる。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

# FastAPIのルーティング
from backend.app.api.status  import router as status_router
from backend.app.api.devices import router as device_router
from backend.app.api.windows import router as window_router

# デバイスの管理を行うサービス群
from backend.app.services.device_manager import DeviceManager
from backend.app.services.measurement_manager import MeasurementManager

# 探索対象のデバイスドライバーの一覧
from backend.app.devices.registry import DEVICE_CLASSES


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPIの起動時・終了時の処理を行う。
    """
    # ========================== 起動処理 ==========================
    
    # デバイスの探索と管理を行うインスタンスを初期化
    device_manager      = DeviceManager(device_classes=DEVICE_CLASSES)
    measurement_manager = MeasurementManager(device_manager=device_manager)
    app.state.device_manager      = device_manager
    app.state.measurement_manager = measurement_manager

    try:
        yield

    finally:
        # ========================== 終了処理 ==========================

        # 1. 全デバイスのsamplingを停止
        # 2. Queueに残っているデータを処理
        # 3. Processor Threadを停止
        measurement_manager.shutdown()

        # 4. 全デバイスとの接続を切断
        device_manager.shutdown()


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

    return app


app = create_app()