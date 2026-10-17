"""
イベント通知の型

主にWabSocket通信で送受信するイベント通知の型を定義
"""

from pydantic import BaseModel


class EventBase(BaseModel):
    """イベント通知の共通属性"""
    type: str           # イベントの種類
    source: str         # イベントの発生源（デバイスIDやシステムなど）
    sequence: int       # イベントのシーケンス番号
    time: str           # イベントの発生時刻


# -----------イベントの種類ごとの型仕様 ------------

class PowerSample(BaseModel):
    """パワーメータのサンプルデータ"""
    power: float        # 光パワー (W)