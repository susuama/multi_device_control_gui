"""WebSocketで配信するイベントの型を定義する。"""

from datetime import datetime as DateTime
from typing import Literal

from pydantic import BaseModel, JsonValue


class MeasurementEvent(BaseModel):
    """1件の取得データと、測定開始基準の経過時間・日時を配信するイベント。"""

    type: Literal["measurement"] = "measurement"
    id: str
    timestamp_ns: int  # デバイスが取得時に付けた単調増加カウンタの値 [ns]。
    elapsed_s: float  # 共通の測定開始時刻からの経過時間 [s]。
    datetime: DateTime  # 測定開始基準の実日時
    data: dict[str, JsonValue]  # 測定値の名前をキーにした、デバイス固有のJSONデータ。
