""" 
APIのステータスを返すエンドポイントを提供するモジュール

GET /api/status
    - アプリケーションの状態を返す
"""

from fastapi import APIRouter

router = APIRouter(
    prefix="/api/status",
    tags=["status"],
)

@router.get("")
def get_status() -> dict[str, str]:
    """アプリケーションの状態を取得する。

    Returns:
        return (dict[str, str]): アプリケーション状態。
    """
    return {
        "status": "ok",
    }
