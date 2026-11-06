"""共通の測定期間と取得状態を参照するAPI。"""

from fastapi import APIRouter, HTTPException, Request

from backend.app.models.measurement import MeasurementStatus


router = APIRouter(prefix="/api/measurements", tags=["measurements"])


@router.get("/status", response_model=MeasurementStatus)
def get_measurement_status(request: Request) -> MeasurementStatus:
    """機器を操作せず、現在の測定状態と保持中の取得エラーを返す。"""
    try:
        return request.app.state.measurement_manager.get_measurement_status()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
