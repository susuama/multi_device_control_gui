""" 
保存処理の開始や終了を行うAPIエンドポイントを定義するモジュール。


POST    /api/recordings/start       // 保存開始
POST    /api/recordings/stop        // 保存終了
GET     /api/recordings/status      // 保存状態・保存先・エラーを取得
"""

from fastapi import APIRouter, HTTPException, Request
from backend.app.models.recordings import RecordingStatus, StartRecordingRequest

router = APIRouter(
    prefix="/api/recordings",
    tags=["recordings"],
)




@router.post("/start", response_model=RecordingStatus)
def start_recording(request: Request, body: StartRecordingRequest) -> RecordingStatus:
    """保存を開始する。

    Args:
        request (Request): FastAPIリクエスト。
        body (StartRecordingRequest): 保存先・記録名と、カメラIDごとの動画保存形式。
    Raises:
        HTTPException: 保存開始に失敗した場合。
    """
    measurement_manager = request.app.state.measurement_manager
    try:
        measurement_manager.start_recording(body.directory, body.name, video_options=body.video_options)
        return measurement_manager.get_recording_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    

@router.post("/stop", response_model=RecordingStatus)
def stop_recording(request: Request) -> RecordingStatus:
    """停止を要求し、動画・CSV・最終メタデータの保存完了を待つ。

    待機中はstatusのis_stoppingがTrueとなり、新しい記録を開始できない。

    Args:
        request (Request): FastAPIリクエスト。
    Raises:
        HTTPException: 保存終了に失敗した場合。
    """
    measurement_manager = request.app.state.measurement_manager
    try:
        measurement_manager.stop_recording()
        return measurement_manager.get_recording_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    

@router.get("/status", response_model=RecordingStatus)
def get_recording_status(request: Request) -> RecordingStatus:
    """保存状態を取得する。

    Args:
        request (Request): FastAPIリクエスト。
    Raises:
        HTTPException: 保存状態の取得に失敗した場合。
    """
    measurement_manager = request.app.state.measurement_manager
    try:
        return measurement_manager.get_recording_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
