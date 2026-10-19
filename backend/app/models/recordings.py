""" 
保存処理のデータ型を定義するモジュール。
"""

from pydantic import BaseModel
from pathlib import Path

class StartRecordingRequest(BaseModel):
    """保存開始リクエストのデータ型。"""
    directory: str | Path       # 保存先の親フォルダ
    name: str | None = None     # 記録フォルダ名

class RecordingStatus(BaseModel):
    """ 保存状態を表すデータ型。"""
    is_recording: bool      # 保存中かどうか
    directory: str | None   # 保存先のディレクトリパス
    name: str | None        # 保存ファイル名
    error: str | None       # 保存中に発生したエラーの内容。保存中でない場合はNone。
