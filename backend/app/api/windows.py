""" 
ウィンドウの操作を行うAPIエンドポイントを定義するモジュール。

GET     /api/windows                // 現在表示されているウィンドウの一覧を取得する
POST    /api/windows                // 新しいpywebviewウィンドウを作成する

以下は実装予定で、現在はエンドポイントを登録していない。
GET     /api/windows/{id}           // 指定されたウィンドウの情報を取得する
DELETE  /api/windows                // 全てのウィンドウを閉じる
DELETE  /api/windows/{id}           // 指定されたウィンドウを閉じる
"""

from fastapi import APIRouter, Request, HTTPException

from backend.app.models.windows import WindowInfo, CreateWindowRequest

router = APIRouter(
    prefix="/api/windows",
    tags=["windows"],
)


@router.get("", response_model=list[WindowInfo])
def get_windows(request: Request) -> list[WindowInfo]:
    """現在表示されているウィンドウの一覧を取得する。

    Args:
        request (Request): FastAPIリクエスト。

    Returns:
        return (list[WindowInfo]): ウィンドウの一覧。
    """
    window_info_list = []

    window_manager = request.app.state.window_manager
    if window_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Window manager is not available.",
        )
    
    # 一覧を取得
    windows = window_manager.get_all()
    for window in windows:
        window_info = WindowInfo(
            id=window.id,
            title=window.title,
            position=(window.x, window.y),
            size=(window.width, window.height),
        )
        window_info_list.append(window_info)
    return window_info_list


@router.post("")
def create_window(value: CreateWindowRequest, request: Request,) -> dict[str, bool]:
    """新しいpywebviewウィンドウを作成する。

    Args:
        value (CreateWindowRequest): ウィンドウ設定。
        request (Request): FastAPIリクエスト。

    Returns:
        return (dict[str, bool]): 作成結果。
    """
    window_manager = request.app.state.window_manager

    if window_manager is None:
        raise HTTPException(
            status_code=503,
            detail="Window manager is not available.",
        )

    # 新しいウィンドウを作成
    window_manager.new_window(
        title=value.title,
        path=value.path,
    )

    return {
        "success": True,
    }
