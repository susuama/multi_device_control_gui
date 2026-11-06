"""WebSocketで配信するイベントの型を定義する。"""

import json
from datetime import datetime as DateTime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class MeasurementEvent(BaseModel):
    """1件の取得データと、測定開始基準の経過時間・日時を配信するイベント。"""

    type: Literal["measurement"] = "measurement"
    server_instance_id: str = Field(min_length=1)
    measurement_id: str = Field(min_length=1)
    id: str
    timestamp_ns: int  # デバイスが取得時に付けた単調増加カウンタの値 [ns]。
    elapsed_s: float  # 共通の測定開始時刻からの経過時間 [s]。
    datetime: DateTime  # 測定開始時の基準から換算した取得日時。
    data: dict[str, JsonValue]  # 測定値の名前をキーにした、デバイス固有のJSONデータ。


class CameraPreviewEvent(BaseModel):
    """圧縮済みJPEGと、その画像に対応するフレーム情報。

    Broadcasterにはmodel_dump()で得た辞書を渡す。JPEGの生成・検査は配信元で行う。
    width・heightはJPEG画像の画素数、timestamp_nsはPC取得時の単調増加カウンタ。
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["camera_preview"] = "camera_preview"
    server_instance_id: str = Field(min_length=1)
    measurement_id: str = Field(min_length=1)
    id: str = Field(min_length=1)
    frame_index: int = Field(ge=0)
    timestamp_ns: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    jpeg: bytes = Field(strict=True, min_length=1, repr=False)

    def to_bytes(self) -> bytes:
        """フレーム情報とJPEGを1件のWebSocketメッセージにまとめる。

        Returns:
            bytes: 4バイトのJSON長（ビッグエンディアン）、UTF-8のJSON、JPEGの順のバイト列。
        """
        metadata = self.model_dump(exclude={"jpeg"})
        # JavaScriptの整数精度による取得時刻の桁落ちを避ける。
        metadata["timestamp_ns"] = str(self.timestamp_ns)
        header = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return len(header).to_bytes(4, "big") + header + self.jpeg
