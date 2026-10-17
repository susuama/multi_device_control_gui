"""
デバイスの情報の取得、設定、測定の開始、停止などの操作を行うAPIエンドポイントを定義するモジュール。


GET  /api/devices                   // DeviceManagerに登録されている全デバイスの情報を取得する
POST /api/devices/scan              // デバイスをスキャンし、検出結果をDeviceManagerへ反映して一覧を返す

GET  /api/devices/{id}              // 指定されたデバイスの情報を取得する
POST /api/devices/{id}/connect      // 指定されたデバイスに接続する
POST /api/devices/{id}/disconnect   // 指定されたデバイスとの接続を切断する

GET  /api/devices/{id}/settings     // 設定情報の取得（未実装、501を返す）
PUT  /api/devices/{id}/settings     // 設定情報の更新（未実装、501を返す）
"""


from fastapi import APIRouter, HTTPException, Request

# デバイスの情報や設定の型定義
from backend.app.models.devices import DeviceInfo, PowerMeterSettings

router = APIRouter(
    prefix="/api/devices",
    tags=["devices"],
)


@router.get("", response_model=list[DeviceInfo])
def get_devices(request: Request) -> list[DeviceInfo]:
    """登録されているデバイス一覧を取得する。

    Args:
        request (Request): FastAPIリクエスト。

    Returns:
        return (list[DeviceInfo]): デバイス一覧。
    """
    device_manager = request.app.state.device_manager
    # 通信ハンドルを含む機器インスタンスではなく、API用の情報だけを返す。
    return [device.get_info() for device in device_manager.get_all().values()]


@router.post("/scan", response_model=list[DeviceInfo])
def scan_devices(request: Request) -> list[DeviceInfo]:
    """デバイスをスキャンして管理対象を更新する。

    Args:
        request (Request): FastAPIリクエスト。

    Returns:
        return (list[DeviceInfo]): 更新後のデバイス一覧。
    """
    device_manager = request.app.state.device_manager
    try:
        # 探索と登録更新は既存のDeviceManagerにまとめて任せる。
        device_manager.scan()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return get_devices(request)


@router.get("/{device_id}", response_model=DeviceInfo)
def get_device(device_id: str, request: Request) -> DeviceInfo:
    """指定されたデバイスの情報を取得する。

    Args:
        device_id (str): デバイスID。
        request (Request): FastAPIリクエスト。

    Returns:
        return (DeviceInfo): デバイスの情報。
    """
    device_manager = request.app.state.device_manager
    try:
        device = device_manager.get(device_id)
    except KeyError as exc:
        # 管理サービスの「機器なし」をAPIの404へ変換する。
        raise HTTPException(status_code=404, detail="Device not found") from exc

    return device.get_info()

@router.post("/{device_id}/connect")
def connect_device(device_id: str, request: Request) -> dict[str, bool]:
    """指定されたデバイスに接続する。

    Args:
        device_id (str): デバイスID。
        request (Request): FastAPIリクエスト。
    Returns:
        return (dict[str, bool]): 接続結果。
    """
    device_manager = request.app.state.device_manager
    try:
        device_manager.get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    try:
        success = device_manager.connect(device_id)
    except Exception as exc:
        # 機器不明の404と区別し、接続・停止待ちの失敗は503として理由を返す。
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=503, detail="Could not connect device")
    return {"success": True}

@router.post("/{device_id}/disconnect")
def disconnect_device(device_id: str, request: Request) -> dict[str, bool]:
    """指定されたデバイスとの接続を切断する。

    Args:
        device_id (str): デバイスID。
        request (Request): FastAPIリクエスト。
    Returns:
        return (dict[str, bool]): 切断結果。
    """
    device_manager = request.app.state.device_manager
    try:
        device_manager.get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    try:
        # 対象機器の取得を止め、管理情報を整理してから切断する。
        # ほかの機器が取得中なら、共通のデータ処理は継続する。
        # 停止がタイムアウトした場合は、切断成功の応答を返さない。
        request.app.state.measurement_manager.stop_sampling(device_id)
        success = device_manager.disconnect(device_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=503, detail="Could not disconnect device")
    return {"success": True}


@router.get(
    "/{device_id}/settings",
    response_model=PowerMeterSettings,
    summary="機器設定の取得（未実装）",
    responses={501: {"description": "機器設定の取得は未実装。"}},
)
def get_device_settings(device_id: str, request: Request) -> PowerMeterSettings:
    """指定されたデバイスの設定情報を取得する（未実装）。

    Args:
        device_id (str): デバイスID。
        request (Request): FastAPIリクエスト。
    Raises:
        HTTPException: 未実装のため501を返す。
    """
    raise HTTPException(status_code=501, detail="Device settings retrieval is not implemented.")


@router.put(
    "/{device_id}/settings",
    response_model=PowerMeterSettings,
    summary="機器設定の更新（未実装）",
    responses={501: {"description": "機器設定の更新は未実装。"}},
)
def update_device_settings(device_id: str, settings: PowerMeterSettings, request: Request) -> PowerMeterSettings:
    """指定されたデバイスの設定情報を更新する（未実装）。

    Args:
        device_id (str): デバイスID。
        settings (PowerMeterSettings): 更新する設定情報。
        request (Request): FastAPIリクエスト。
    Raises:
        HTTPException: 未実装のため501を返す。
    """
    raise HTTPException(status_code=501, detail="Device settings update is not implemented.")
