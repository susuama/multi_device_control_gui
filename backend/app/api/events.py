"""購読キューのイベントをWebSocket接続へ配信する。"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.app.models.events import CameraPreviewEvent, MeasurementEvent


router = APIRouter(
    prefix="/ws",
    tags=["websocket"],
)


@router.websocket("")
@router.websocket("/{device_id}")
async def websocket_events(
    websocket: WebSocket,
    maxsize: Annotated[int, Query(ge=1)] = 100,
) -> None:
    """指定キーのイベントを購読し、イベントの種類に応じた形式で配信する。

    Args:
        websocket (WebSocket): WebSocket接続。
        maxsize (int): 接続用キューの保持件数。1以上をクエリ引数で指定する。
    """
    device_id = websocket.path_params.get("device_id")
    await _stream_events(websocket, key=device_id, maxsize=maxsize)


async def _stream_events(websocket: WebSocket, *, key: str | None, maxsize: int) -> None:
    """イベントを送信し、切断または配信終了時に購読を解除する。

    Args:
        websocket (WebSocket): WebSocket接続。
        key (str | None): 購読するイベントの配信キー。
        maxsize (int): 接続用キューの保持件数。
    """
    tasks: list[asyncio.Task[None]] = []
    event_broadcaster = websocket.app.state.event_broadcaster

    # 接続用のQueueを作成し、配信対象に登録する
    queue = event_broadcaster.subscribe(key=key, maxsize=maxsize)

    async def send_events() -> None:
        """キューのイベントを送信する。Noneを受け取ったら接続を閉じる。"""
        while True:
            event = await queue.get()
            try:
                if event is None:
                    await websocket.close()
                    return
                
                # イベントの種類に応じて送信形式を変える
                event_type = event.get("type")
                if event_type == "measurement":
                    measurement = MeasurementEvent.model_validate(event)
                    await websocket.send_json(measurement.model_dump(mode="json"))
                elif event_type == "camera_preview":
                    preview = CameraPreviewEvent.model_validate(event)
                    await websocket.send_bytes(preview.to_bytes())
                else:
                    raise ValueError(f"Unsupported event type: {event_type!r}")
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
