"""
アプリ全体のエントリーポイント。

アプリ起動
↓
WindowManager インスタンスを作成
↓
FastAPIにWindowManager インスタンスを共有
↓
別スレッドでFastAPI起動
↓
メインスレッドでウィンドウを表示
↓
ウィンドウ終了
↓
FastAPIを正常終了
"""

import threading

import uvicorn

from backend.app.main import app
from desktop.main import WindowManager


def main() -> None:
    """アプリケーション全体を起動する。"""
    window_manager = WindowManager(
        base_url="http://127.0.0.1:5173",
    )

    # FastAPIからWindowManagerを参照できるようにする
    app.state.window_manager = window_manager

    # Uvicornサーバーを作成
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=8000,
        reload=False,
    )
    server = uvicorn.Server(config)

    # FastAPIを別スレッドで起動
    server_thread = threading.Thread(
        target=server.run,
        name="fastapi-server",
    )
    server_thread.start()

    try:
        # メインウィンドウ作成して表示する
        window_manager.create_main_window()
        window_manager.start()

    finally:
        # pywebview終了後、FastAPIへ終了要求
        server.should_exit = True
        server_thread.join()


if __name__ == "__main__":
    main()