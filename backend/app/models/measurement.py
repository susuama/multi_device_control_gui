"""
測定データや測定全体の情報の型
"""
from dataclasses import dataclass
from typing import Literal


@dataclass
class DeviceData:
    """デバイスから共通Queueへ渡す取得データ・設定通知。"""
    id: str
    type: Literal["sample", "settings"]
    timestamp_ns: int
    data: dict[str, object]
