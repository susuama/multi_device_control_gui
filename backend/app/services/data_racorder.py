""" 
データの保存を行う

"""

import csv
import re
from datetime import datetime
from pathlib import Path

from backend.app.models.recordings import RecordingStatus

from backend.app.utils.logging_utils import setup_logger

class DataRecorder:
    def __init__(self):
        """初期化"""
        self._is_recording = False
        self.directory = None
        self.name = None
        self.error = None
        self._files = {}
        self._writers = {}

        self.logger = setup_logger(__class__.__name__)

    def get_status(self) -> RecordingStatus:
        """記録状態を返す。"""
        return RecordingStatus(
            is_recording=self._is_recording,
            directory=self.directory,
            name=self.name,
            error=self.error
        )

    def start(self, directory: str, name: str | None = None) -> None:
        """記録用フォルダを作成する。ファイルは最初のサンプル受信時に作成する。"""
        if self._is_recording:
            raise RuntimeError("Recording has already started.")

        self.error = None
        try:
            path = Path(directory).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir():
                raise NotADirectoryError(directory)
            
            self.directory = str(path)
            self.name = name or datetime.now().strftime("%Y-%m-%d_%H%M%S")
            self._is_recording = True
            
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            raise

    def write(self, sample, elapsed_s: float | None = None) -> None:
        """取得データをデバイスごとのCSVへ追記する。"""
        if not self._is_recording:
            return

        try:
            device_id = sample.id
            row = sample.model_dump() if hasattr(sample, "model_dump") else vars(sample).copy()
            if elapsed_s is not None:
                row["elapsed_s"] = elapsed_s

            if device_id not in self._writers:
                prefix = re.sub(r"[^\w.-]+", "_", self.name).strip("._") or "recording"
                safe_id = re.sub(r"[^\w.-]+", "_", device_id).strip("._") or "device"
                path = Path(self.directory) / f"{prefix}_{safe_id}.csv"
                file = path.open("x", newline="", encoding="utf-8-sig")
                try:
                    writer = csv.DictWriter(file, fieldnames=list(row))
                    writer.writeheader()
                except Exception:
                    file.close()
                    raise
                self._files[device_id] = file
                self._writers[device_id] = writer

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
