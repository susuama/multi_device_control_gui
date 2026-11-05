"""
測定データや測定全体の情報の型
"""
from dataclasses import dataclass
from typing import Literal


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
