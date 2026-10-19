"""
API通信時のデバイス情報の型を定義

機器の情報や状態、機器固有の設定

デバイス固有の設定
デバイスタイプ（power-meterなど）ごとに型を定義する。

（PM160などのデバイスごとの型定義はしない）
"""


from typing import Literal
from pydantic import BaseModel, ConfigDict


class DeviceInfo(BaseModel):
    """デバイスの共通型仕様"""
    id: str             # デバイス固有のID。プログラム側での識別用。接続時に登録する。
    type: str           # デバイスタイプ （カメラ、パワーメータ...）
    manufacturer: str   # メーカー名
    model: str          # モデル名
    serial_number: str  # シリアル番号。機器に登録された値
    address: str        # デバイスのアドレス。
    status: Literal[    # デバイスの状態
        "connected",
        "disconnected",
        "error",
    ]       



class DiscoveredDevice(BaseModel):
    """スキャンで発見されたデバイスの情報。"""
    driver: str                         # デバイスドライバーの識別名（例: "dummy_power_meter"）
    type: str
    manufacturer: str | None = None
    model: str
    serial_number: str | None = None
    address: str


#  ------------デバイスタイプごとの型仕様 ------------

class CameraSetting(BaseModel):
    """カメラ固有の属性"""
    pass



class PowerMeterSettings(BaseModel):
    """パワーメータ固有の属性"""
    # 定義されていない設定名を受け取ると、検証エラーにする。
    model_config = ConfigDict(extra="forbid")

    wavelength: float   # 波長 (nm)
    range: float        # 測定範囲 (W)
    auto_range: bool    # 自動レンジ切替の有無
    bandwidth: str      # 帯域幅 (low or high)
    averaging: int      # (内部)平均化の点数


