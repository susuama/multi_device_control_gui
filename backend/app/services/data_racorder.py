""" 
データの保存を行う

"""

import csv
import re
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from backend.app.models.measurement import DeviceData
from backend.app.models.recordings import RecordingStatus

from backend.app.utils.logging_utils import setup_logger

class DataRecorder:
    def __init__(self):
        """初期化"""
        self._is_recording = False
        self.directory = None
        self.name = None
        self.error = None
        self.metadata = {}      # メタデータ。記録設定やデバイスごとの設定を保持する。
        self._files = {}        # ファイルパスの一覧。キーはデバイスID、値はPathオブジェクト
        self._writers = {}      # 記録中のデバイス。キーはデバイスID、値はcsv.DictWriter

        self.logger = setup_logger(__class__.__name__)

    def get_status(self) -> RecordingStatus:
        """記録状態を返す。"""
        return RecordingStatus(
            is_recording=self._is_recording,
            directory=str(self.directory) if self.directory is not None else None,
            name=self.name,
            error=self.error
        )

    def start(self, directory: str, name: str | None = None) -> None:
        """
        記録用フォルダを作成する。CSVは最初のサンプル受信時に作成する。

        Args:
            directory (str): 保存先の親フォルダ
            name (str | None): 保存フォルダ名。Noneの場合は現在時刻を使用する。
        Raises:
            RuntimeError: すでに記録中の場合。
            Exception: フォルダ作成に失敗した場合。
        """
        if self._is_recording:
            raise RuntimeError("Recording has already started.")

        self.error = None
        try:
            # 保存フォルダ名が指定されなければ現在時刻
            recording_name = name or datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
            if Path(recording_name).name != recording_name or recording_name in {".", ".."}:
                raise ValueError("Recording name must be a single folder name.")
            
            # 親フォルダを作成
            parent_directory = Path(directory).expanduser()
            parent_directory.mkdir(parents=True, exist_ok=True)
            recording_directory = parent_directory / recording_name

            # 保存フォルダを記録ごとに新規作成。上書きしない。
            recording_directory.mkdir()

            # 記録用のフォルダを設定
            self.directory = recording_directory    # 記録フォルダパス
            self.name = recording_name              # 記録フォルダ名
            self.metadata = {}                      # 今回の記録のメタデータを初期化する

            # 記録の開始
            self._is_recording = True
            
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            raise

    def write_metadata(self, metadata: dict) -> None:
        """作成済みの記録フォルダへメタデータをJSON形式で保存する。

        記録停止後も、終了日時を含む最終メタデータを保存できる。

        Args:
            metadata (dict): メタデータ。記録設定やデバイスごとの設定を保持する。
        Raises:
            RuntimeError: 記録フォルダが未作成の場合。
            Exception: メタデータの保存に失敗した場合。
        """
        if self.directory is None:
            raise RuntimeError("Recording has not started.")

        temporary_path = None
        try:
            # 渡された辞書をコピーし、ファイルを開く前にJSON文字列へ変換する。
            saved_metadata = deepcopy(metadata)
            contents = json.dumps(saved_metadata, ensure_ascii=False, indent=4)
            metadata_path = Path(self.directory) / "metadata.json"

            # 同じ記録フォルダ内に一時ファイルを作成し、JSON全体を書き込む。
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory,
                prefix=".metadata-", suffix=".tmp", delete=False,
            ) as file:
                temporary_path = Path(file.name)
                file.write(contents)

            # 一時ファイルを閉じてから、直前のmetadata.jsonと置き換える。
            temporary_path.replace(metadata_path)
            self.metadata = saved_metadata
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            raise
        finally:
            # 保存に失敗して残った一時ファイルを削除する。
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def write(self, sample: DeviceData, elapsed_s: float | None = None) -> None:
        """記録フォルダ内の「デバイスID.csv」へ取得データを追記する。

        保存先にはstart()で設定したself.directoryを使用する。
        デバイスの最初のサンプルでCSVを新規作成し、以降は同じCSVへ追記する。
        """
        if not self._is_recording:
            return

        try:
            if sample.type != "sample":
                raise ValueError("Only sample data can be written to CSV.")
            device_id = sample.id
            # 共通の識別情報とdata内の測定値をCSVの列へ展開する。
            row = {"id": device_id, "timestamp_ns": sample.timestamp_ns, **sample.data}
            if elapsed_s is not None:
                row["elapsed_s"] = elapsed_s

            # デバイスごとのCSVファイルを作成する。すでに作成済みなら追記する。
            if device_id not in self._writers:
                # デバイスIDをファイル名に使えるように変換する
                safe_id = re.sub(r"[^\w.-]+", "_", device_id).strip("._") or "device"
                filename = f"{safe_id}.csv"
                used_names = {Path(file.name).name.casefold() for file in self._files.values()}
                suffix = 2
                # 文字の置換やWindowsの大文字・小文字の同一視で衝突したら連番を付ける。
                while filename.casefold() in used_names:
                    filename = f"{safe_id}_{suffix}.csv"
                    suffix += 1
                path = Path(self.directory) / filename

                # CSVファイルを新規作成し、ヘッダを書き込む
                file = path.open("x", newline="", encoding="utf-8-sig")
                try:
                    writer = csv.DictWriter(file, fieldnames=list(row))
                    writer.writeheader()
                except Exception:
                    file.close()
                    raise
                self._files[device_id] = file       # ファイルパスを保持する
                self._writers[device_id] = writer   # CSVライターを保持する
                self.logger.debug("Created CSV file: %s", path)
                
            # CSVライターを取得して追記する
            self._writers[device_id].writerow(row)  
            self.logger.debug("Writing sample: %s", sample)
            
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            try:
                self.stop()
            finally:
                raise

    def stop(self) -> None:
        """ファイルを閉じて記録を終了する。"""
        self._is_recording = False
        error = None
        for file in self._files.values():
            try:
                file.close()
            except Exception as exc:
                error = exc

        self._files.clear()
        self._writers.clear()
        if error is not None:
            self.error = str(error) or type(error).__name__
            raise error

    def is_recording(self) -> bool:
        """記録中かどうかを返す。"""
        return self._is_recording
