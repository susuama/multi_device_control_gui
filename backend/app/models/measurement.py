"""
測定データや測定全体の情報の型
"""
from dataclasses import dataclass


@dataclass
class PowerSample:
    """パワーメータのデータ一点"""
    id: str
    timestamp_ns: int
    power_w: float