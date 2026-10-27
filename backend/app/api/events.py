"""購読キューのイベントをWebSocket接続へ配信する。"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect


router = APIRouter(
    prefix="/ws",
    tags=["websocket"],
)


@router.websocket("")
async def websocket_events(websocket: WebSocket) -> None:
    """イベントを送信し、切断または配信終了時に購読を解除する。

    Args:
        websocket (WebSocket): WebSocket接続。
    """
    tasks: list[asyncio.Task[None]] = []
    event_broadcaster = websocket.app.state.event_broadcaster

    # 接続用のQueueを作成し、配信対象に登録する
    queue = event_broadcaster.subscribe()

    async def send_events() -> None:
        """キューのイベントを送信する。Noneを受け取ったら接続を閉じる。"""
        while True:
            event = await queue.get()
            try:
                if event is None:
                    await websocket.close()
                    return
                await websocket.send_json(event)
            finally:
                # 終了通知や送信失敗の場合も、取り出した1件を未完了タスク数から除く。
                queue.task_done()

    async def wait_for_disconnect() -> None:
        """クライアントの送信内容は使わず、切断通知まで受信を続ける。"""
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    try:
        await websocket.accept()

        # Queueの受信待機中でも切断を検知するため、送信と受信を別タスクで実行する。
        tasks = [
            asyncio.create_task(send_events()), 
            asyncio.create_task(wait_for_disconnect())
            ]
        # イベント配信の終了、またはWebSocketの切断を待つ。
        done, _ = await asyncio.wait(
            tasks, 
            return_when=asyncio.FIRST_COMPLETED
            )
        
        for task in done:
            task.result()   # 終了タスクの例外をここで発生させる
            
    except WebSocketDisconnect:
        pass

    finally:
        # 接続終了時に購読登録を解除する
        event_broadcaster.unsubscribe(queue)

        # 残っているキュー待機・受信待機をキャンセル。タスクの終了まで待つ。
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
