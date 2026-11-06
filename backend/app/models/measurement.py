"""
測定データや測定全体の情報の型
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


@dataclass(frozen=True)
class MeasurementPeriod:
    """測定IDと共通の時間基準。同じ基準で取得する機器間で共有する。"""

    measurement_id: str
    started_at: datetime
    start_counter_ns: int


class MeasurementStatus(BaseModel):
    """直近の測定期間と、現在取得中の機器を返す。初回取得前の期間はNone。"""

    server_instance_id: str
    measurement_id: str | None
    started_at: datetime | None
    sampling_device_ids: list[str]
    error: str | None


@dataclass
class CameraFrame:
    """画像とカメラ由来のフレーム情報。DeviceData.dataに格納する。

    imageはC連続のuint8配列（buffer protocol対応）を渡す。
    mono8は(高さ, 幅)、rgb24・bgr24は(高さ, 幅, 3)で、色成分の順序はCameraSettingに従う。
    共通Queueへ渡した後は、保存用ROIとプレビュー用画像のコピーが終わるまで変更しない。
    device_timestamp_nsはカメラで取得できる時間をnsへ換算した値。PCの時計とは基準が異なる。
    PC側の取得時刻は外側のDeviceData.timestamp_nsへ格納する。
    """
    image: object
    frame_index: int
    device_timestamp_ns: int | None = None


@dataclass
class DeviceData:
    """共通Queueへ渡すデータ。frameはCameraFrame、それ以外は辞書を格納する。"""
    id: str
    type: Literal["sample", "settings", "frame"]
    timestamp_ns: int
    data: dict[str, object] | CameraFrame
