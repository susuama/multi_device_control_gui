"""
API通信時のデバイス情報の型を定義

機器の情報や状態、機器固有の設定

デバイス固有の設定
デバイスタイプ（power-meterなど）ごとに型を定義する。

（PM160などのデバイスごとの型定義はしない）
"""


from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class DeviceInfo(BaseModel):
    """デバイスの共通型仕様"""
    id: str             # デバイス固有のID。プログラム側での識別用。接続時に登録する。
    type: str           # デバイスタイプ （カメラ、パワーメータ...）
    manufacturer: str   # メーカー名
    model: str          # モデル名
    serial_number: str  # シリアル番号。機器に登録された値
    address: str        # デバイスのアドレス。
    error: str | None = None  # 接続・取得・後始末で保持しているエラー理由。
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

class CameraROI(BaseModel):
    """元画像の左上を原点とする、ソフトウェア保存用の切り出し範囲。[pixel]"""
    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class CameraSetting(BaseModel):
    """カメラが保持する撮影条件と保存ROI。画素はモノクロまたはRGB/BGRの各成分8 bit。"""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    width: int = Field(gt=0)        # ROI切り出し前の取得画像の幅 [pixel]。
    height: int = Field(gt=0)       # ROI切り出し前の取得画像の高さ [pixel]。
    fps: float = Field(gt=0)        # 機器へ適用された取得FPS。
    exposure: float = Field(gt=0)   # 露光時間 [s]。
    gain: float = Field(ge=0)       # 機器へ適用されたゲイン係数。
    gain_boost: bool = False        # ゲインブーストの有無。
    pixel_format: Literal["mono8", "rgb24", "bgr24"] = "mono8"
    roi: CameraROI | None = None    # Noneなら取得画像全体を保存する。

    @model_validator(mode="after")
    def validate_roi(self) -> "CameraSetting":
        """保存ROIが取得画像内に収まっていることを確認する。"""
        if self.roi is not None:
            if self.roi.x + self.roi.width > self.width or self.roi.y + self.roi.height > self.height:
                raise ValueError("ROI must fit within the acquired image.")
        return self



class PowerMeterSettings(BaseModel):
    """パワーメータ固有の属性"""
    # 定義されていない設定名を受け取ると、検証エラーにする。
    model_config = ConfigDict(extra="forbid")

    wavelength: float   # 波長 (nm)
    range: float        # 測定範囲 (W)
    auto_range: bool    # 自動レンジ切替の有無
    bandwidth: str      # 帯域幅 (low or high)
    averaging: int      # (内部)平均化の点数


