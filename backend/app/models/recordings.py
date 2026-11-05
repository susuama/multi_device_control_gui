""" 
保存処理のデータ型を定義するモジュール。
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pathlib import Path


@dataclass
class VideoFrameRecord:
    """動画の1フレームに対応するCSVの列。宣言順をCSVの列順として使う。"""

    video_frame_index: int              # 動画内のフレーム番号。0始まり。
    frame_index: int                    # 取得側が付けた元のフレーム番号。
    timestamp_ns: int                   # PC側の取得カウンタ時刻 [ns]。
    device_timestamp_ns: int | None     # カメラ側の時刻 [ns]。取得できなければNone。
    elapsed_s: float | None             # 共通の測定開始からの経過時間 [s]。


class VideoRecordingOptions(BaseModel):
    """動画の保存形式。撮影条件とは分けて記録開始時に指定する。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["h264_lossless", "ffv1", "mpeg4"]

    @property
    def extension(self) -> str:
        """保存形式に対応するコンテナの拡張子を返す。"""
        return ".mp4" if self.format == "mpeg4" else ".mkv"


class RecordingTarget(BaseModel):
    """記録対象のタイプと、開始時の機器設定・保存設定。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    settings: dict[str, JsonValue]
    video_options: VideoRecordingOptions | None = None


class StartRecordingRequest(BaseModel):
    """保存開始リクエストのデータ型。"""
    directory: str | Path       # 保存先の親フォルダ
    name: str | None = None     # 記録フォルダ名
    video_options: dict[str, VideoRecordingOptions] = Field(default_factory=dict)

class RecordingStatus(BaseModel):
    """記録全体の状態。保存先・記録名・エラーは停止後も保持する。"""
    is_recording: bool      # 新しい測定データを保存する状態にあるか。
    is_stopping: bool = False  # 新規受付を止め、保存の終了処理を行っているか。
    directory: str | None   # 作成した記録フォルダのパス。未作成ならNone。
    name: str | None        # 記録フォルダ名。未作成ならNone。
    error: str | None       # 記録処理で保持しているエラー理由。エラーがなければNone。
