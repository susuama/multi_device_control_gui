"""
デバイスの情報の取得、設定、測定の開始、停止などの操作を行うAPIエンドポイントを定義するモジュール。


GET  /api/devices                   // DeviceManagerに登録されている全デバイスの情報を取得する
POST /api/devices/scan              // デバイスをスキャンし、検出結果をDeviceManagerへ反映して一覧を返す

GET  /api/devices/{id}              // 指定されたデバイスの情報を取得する
POST /api/devices/{id}/connect      // 指定されたデバイスに接続する
POST /api/devices/{id}/disconnect   // 指定されたデバイスとの接続を切断する

GET  /api/devices/{id}/settings     // 機器固有の設定を辞書で取得する
PUT  /api/devices/{id}/settings     // 辞書で受け取った設定を検証して更新する

POST /api/devices/{device_id}/sampling/start        // 指定したデバイスのデータ取得を開始する
POST /api/devices/{device_id}/sampling/stop         // 指定したデバイスのデータ取得を停止する
GET  /api/devices/{device_id}/sampling              // 指定したデバイスがデータ取得中か確認する
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

# デバイスの情報や設定の型定義
from backend.app.models.devices import DeviceInfo

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
    measurement_manager = request.app.state.measurement_manager
    try:
        device_manager.get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    try:
        # 対象機器の取得を止め、管理情報を整理してから切断する。
        # ほかの機器が取得中なら、共通のデータ処理は継続する。
        # 停止がタイムアウトした場合は、切断成功の応答を返さない。
        measurement_manager.stop_sampling(device_id)
        success = device_manager.disconnect(device_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=503, detail="Could not disconnect device")
    return {"success": True}


@router.get(
    "/{device_id}/settings",
    response_model=dict[str, Any],
    summary="機器設定の取得",
    responses={
        404: {"description": "指定されたデバイスが存在しない。"},
        503: {"description": "機器設定の取得に失敗した。"},
    },
)
def get_device_settings(device_id: str, request: Request) -> dict[str, Any]:
    """指定されたデバイスの設定情報をJSON対応の辞書で取得する。

    Args:
        device_id (str): デバイスID。
        request (Request): FastAPIリクエスト。
    Raises:
        HTTPException: デバイスが存在しない場合は404、取得に失敗した場合は503を返す。
    """
    device_manager = request.app.state.device_manager
    try:
        device_manager.get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc

    try:
        # デバイス固有の設定モデルを、APIの共通形式である辞書へ変換する。
        settings = device_manager.get_settings(device_id)
        return settings.model_dump(mode="json")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put(
    "/{device_id}/settings",
    response_model=dict[str, Any],
    summary="機器設定の更新",
    responses={
        404: {"description": "指定されたデバイスが存在しない。"},
        503: {"description": "機器設定の更新に失敗した。"},
    },
)
def update_device_settings(
    device_id: str, settings: dict[str, Any], request: Request,
) -> dict[str, Any]:
    """設定一式をデバイス側で検証・適用し、適用後の設定を辞書で返す。

    Args:
        device_id (str): デバイスID。
        settings (dict[str, Any]): デバイス固有の設定一式。
        request (Request): FastAPIリクエスト。
    Raises:
        HTTPException: デバイス不明は404、設定の検証エラーは422、更新失敗は503を返す。
    """
    device_manager = request.app.state.device_manager
    try:
        device_manager.get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc

    try:
        # 辞書を対象デバイスへ渡し、デバイス自身の設定モデルで検証・適用する。
        applied_settings = device_manager.set_settings(device_id, settings)
        return applied_settings.model_dump(mode="json")
    except ValidationError as exc:
        # 項目の位置にbodyを付け、JSON化できる検証エラーの一覧を返す。
        errors = exc.errors(include_url=False, include_context=False)
        for error in errors:
            error["loc"] = ["body", *error["loc"]]
        raise HTTPException(status_code=422, detail=errors) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{device_id}/sampling/start")
def start_sampling(device_id: str, request: Request) -> dict[str, bool]:
    """指定されたデバイスのデータ取得を開始する。

    Args:
        device_id (str): デバイスID。
        request (Request): FastAPIリクエスト。
    Returns:
        return (dict[str, bool]): データ取得開始結果。
    """
    device_manager = request.app.state.device_manager
    measurement_manager = request.app.state.measurement_manager
    try:
        device_manager.get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    try:
        measurement_manager.start_sampling(device_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True}

@router.post("/{device_id}/sampling/stop")
def stop_sampling(device_id: str, request: Request) -> dict[str, bool]:
    """指定されたデバイスのデータ取得を停止する。

    Args:
        device_id (str): デバイスID。
        request (Request): FastAPIリクエスト。
    Returns:
        return (dict[str, bool]): データ取得停止結果。
    """ 
    device_manager = request.app.state.device_manager
    measurement_manager = request.app.state.measurement_manager
    try:
        device_manager.get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    try:
        measurement_manager.stop_sampling(device_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"success": True}

@router.get("/{device_id}/sampling")
def is_sampling(device_id: str, request: Request) -> dict[str, bool]:
    """指定されたデバイスがデータ取得中か確認する。

    Args:
        device_id (str): デバイスID。
        request (Request): FastAPIリクエスト。
    Returns:
        return (dict[str, bool]): データ取得中かどうか。
    """
    device_manager = request.app.state.device_manager
    measurement_manager = request.app.state.measurement_manager
    try:
        device_manager.get(device_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Device not found") from exc
    try:
        is_sampling = measurement_manager.is_sampling(device_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"is_sampling": is_sampling}
