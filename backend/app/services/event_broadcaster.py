"""
別スレッドから受け取ったイベントを、キーが一致する購読者へ配信する。


1. EventBroadcaster → WebSocket API
- WebSocket接続時にsubscribe()を呼び出す。
- subscribe()は接続ごとに、指定されたキー・保持数のQueueを作成・登録する。
- WebSocket APIはawait queue.get()でイベントを取り出し、用途に応じた形式で送信する。
- 切断時にはunsubscribe()で登録を解除する。

2. MeasurementManager → EventBroadcaster
- MeasurementManagerなどの配信元はpublish(event, key=...)でイベントを渡す。
- publish()はイベントループに_broadcast(event)の実行を予約する。
- _broadcast()が指定キーと一致するQueueにイベントを追加する。
"""

from asyncio import AbstractEventLoop, Queue
from threading import Lock
from typing import Any


class EventBroadcaster:
    """イベントループと、接続ごとの配信キューを管理する。"""

    def __init__(self) -> None:
        """イベントループ・購読キュー・配信状態を保持するための管理情報を初期化する。"""
        self._loop: AbstractEventLoop | None = None     # イベントの配信先となるイベントループ。FastAPIの起動処理で登録する。
        self._subscribers: dict[Queue[dict[str, Any] | None], str | None] = {}  # 購読キューと配信対象キー。
        self._closed = False        # イベントの配信受付を終了したかどうか。終了後はpublish()での配信予約を無視する。
        self._state_lock = Lock()

    def start(self, loop: AbstractEventLoop) -> None:
        """イベントの配信先となるイベントループを登録し、配信の受付を開始する。

        FastAPIの起動処理から呼び出し、WebSocketが動作するループを渡す。
        同じインスタンスの再起動は行わず、起動ごとに新しく作成する。

        Args:
            loop (AbstractEventLoop): WebSocketの送受信を実行するイベントループ。
        """
        with self._state_lock:
            # 再登録や別ループの登録は許可しない。
            if self._closed:
                raise RuntimeError("EventBroadcaster has been shut down.")
            if self._loop is not None:
                raise RuntimeError("EventBroadcaster has already been started.")
            
            self._loop = loop

    def is_running(self) -> bool:
        """配信の受付が開始されているかどうかを返す。

        Returns:
            bool: 配信の受付が開始されている場合はTrue、終了済みまたは未起動の場合はFalse
        """
        with self._state_lock:
            return self._loop is not None and not self._closed

    def subscribe(
        self, *, key: str | None = None, maxsize: int = 100,
    ) -> Queue[dict[str, Any] | None]:
        """指定キーのイベントを受け取る、上限付きの購読キューを登録する。

        WebSocketの接続時に、登録済みのイベントループ上から呼び出す。

        Args:
            key (str | None): 購読する配信キー。NoneはNoneのイベントだけを受け取る。
            maxsize (int): キューの保持件数。1以上の整数を指定する。

        Returns:
            Queue[dict[str, Any] | None]: イベントを受け取るキュー。
                Noneは配信終了の通知を表す。
        """
        if self._loop is None:
            raise RuntimeError("EventBroadcaster is not running.")
        if key is not None and not isinstance(key, str):
            raise TypeError("Key must be a string or None.")
        if isinstance(maxsize, bool) or not isinstance(maxsize, int):
            raise TypeError("Queue maxsize must be an integer.")
        if maxsize < 1:
            raise ValueError("Queue maxsize must be greater than zero.")
        
        subscriber: Queue[dict[str, Any] | None] = Queue(maxsize=maxsize)
        self._subscribers[subscriber] = key
        return subscriber

    def unsubscribe(self, subscriber: Queue[dict[str, Any] | None]) -> None:
        """購読者のキューを配信対象から取り除く。

        WebSocketの切断時に、登録済みのイベントループ上から呼び出す。

        Args:
            subscriber (Queue[dict[str, Any] | None]): 登録を解除する購読キュー。
        """
        self._subscribers.pop(subscriber, None)

    def publish(self, event: dict[str, Any], *, key: str | None) -> None:
        """指定キーのイベントの配信処理を、登録済みのイベントループへ渡す。

        MeasurementManagerの監視スレッドや画像変換スレッドから呼び出せる。
        call_soon_threadsafe()で_broadcast()を予約し、通信の完了は待たない。
        起動前・終了後のイベントは配信しない。
        イベントは全購読者で共有するため、呼び出し後は内容を変更しない。

        Args:
            event (dict[str, Any]): 配信するイベント。JPEGのbytesなども含められる。
            key (str | None): 配信先のキー。NoneはNoneの購読者だけを対象とする。
        """
        with self._state_lock:
            if self._loop is None:  # 起動前、終了後のイベントは配信しない。
                return
            if key is not None and not isinstance(key, str):
                raise TypeError("Key must be a string or None.")
            
            # イベントループ上で_broadcast()を呼び出す予約を行う。
            self._loop.call_soon_threadsafe(self._broadcast, event, key)

    def _broadcast(self, event: dict[str, Any], key: str | None) -> None:
        """配信キーが一致する各購読キューへイベントを追加する。

        イベントループ上で実行し、キューが満杯の場合は最も古いイベントを
        取り除いてから追加する。配信終了後のイベントは追加しない。

        Args:
            event (dict[str, Any]): 各購読者へ渡すイベント。
            key (str | None): 配信対象とする購読キー。
        """
        # 終了前に予約され、終了後に実行された配信もここで止める。
        if self._closed:
            return
        
        for subscriber, subscriber_key in self._subscribers.items():
            if subscriber_key != key:
                continue
            if subscriber.full():
                subscriber.get_nowait()
                subscriber.task_done()      # 破棄したイベントを未完了タスク数から除外する
            subscriber.put_nowait(event)

    def shutdown(self) -> None:
        """配信の受付を終了し、購読者へ終了を通知する。

        FastAPIの終了処理から、登録済みのイベントループ上で呼び出す。
        
        各購読キューにNoneを追加し、購読登録を解除する。
        キューが満杯の場合は最古のイベントを破棄して終了通知を優先する。
        破棄されずに残ったイベントは、終了通知より先に取り出される。
        """
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._loop = None

        for subscriber in self._subscribers:
            if subscriber.full():
                subscriber.get_nowait()
                subscriber.task_done()  # 破棄したイベントを未完了タスク数から除外する
            subscriber.put_nowait(None)

        self._subscribers.clear()
