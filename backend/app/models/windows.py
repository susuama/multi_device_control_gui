""" 
ウィンドウの操作を行うAPIエンドポイントで使用するモデルの型を定義
"""

from pydantic import BaseModel


class WindowInfo(BaseModel):
    """ウィンドウの情報"""
    id: str
    title: str
    position: tuple[int, int]
    size: tuple[int, int]


class CreateWindowRequest(BaseModel):
    """新しいウィンドウを作成するためのリクエスト"""
    title: str
    path: str