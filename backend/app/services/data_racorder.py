""" 
データの保存を行う

"""

import csv
import re
import json
import queue
import shutil
import subprocess
import threading
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from backend.app.models.devices import CameraSetting
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.models.recordings import RecordingStatus, RecordingTarget, VideoFrameRecord, VideoRecordingOptions

from backend.app.utils.logging_utils import setup_logger
from backend.app.utils.time_utils import get_datetime

class DataRecorder:
    """保存先と記録対象を保持し、保存の開始・受付・非同期の終了処理を管理する。"""
    def __init__(self):
        """初期化"""
        self._lock = threading.RLock()  # 保存状態とメタデータの更新を同期する。
        self._is_recording = False
        self._is_stopping = False
        self._session_open = False      # 終了処理が必要な保存先・リソースがあるか。
        self._finish_thread = None
        self._stop_done = threading.Event()
        self._stop_done.set()
        self._stop_error: Exception | None = None
        self._stop_metadata = None
        self._metadata_pending = False  # 最終JSONの保存に失敗し、再保存が必要か。
        self._recording_stopped_at = None

        self.directory = None
        self.name = None
        self.error = None
        self.metadata = {}              # メタデータ。記録設定やデバイスごとの設定を保持する。
        self._device_ids = frozenset()  # 記録開始時に確定した対象デバイスID。
        self._files = {}                # キーはデバイスID、値は開いているCSVファイル。
        self._csv_writers = {}          # キーはデバイスID、値はcsv.DictWriter。
        self._video_recorders = {}      # キーはカメラID、値はVideoRecorder。

        self.logger = setup_logger(__class__.__name__)

    def get_status(self) -> RecordingStatus:
        """記録状態を返す。
        
        Returns:
            RecordingStatus: 記録状態の情報を保持するオブジェクト
                - is_recording (bool): 新しい測定データを保存する状態にあるか。
                - is_stopping (bool): 保存の終了処理中、または再待機が必要か。
                - directory (str | None): 作成した記録フォルダのパス。停止後も保持する。
                - name (str | None): 記録フォルダ名。停止後も保持する。
                - error (str | None): エラー理由。停止後も保持し、エラーがなければNone。
        """
        with self._lock:
            if self.error is None:
                for recorder in tuple(self._video_recorders.values()):
                    if recorder.error is not None:
                        self.error = recorder.error
                        break

            return RecordingStatus(
                is_recording=self._is_recording,
                is_stopping=self._is_stopping,
                directory=str(self.directory) if self.directory is not None else None,
                name=self.name,
                error=self.error,
            )

    def start(
        self, directory: str, name: str | None = None, *, targets: dict[str, RecordingTarget],
    ) -> None:
        """記録フォルダと対象デバイスのCSVライター・動画保存処理を作成する。

        列名とヘッダーは各デバイスの最初のサンプルで確定する。

        Args:
            directory (str): 保存先の親フォルダ
            name (str | None): 保存フォルダ名。Noneの場合は現在時刻を使用する。
            targets (dict[str, RecordingTarget]): 記録対象IDごとの種類と開始時の機器設定・保存設定。
        Raises:
            RuntimeError: すでに記録中、または前回の終了処理が完了していない場合。
            Exception: フォルダ・ファイル・ライターの作成に失敗した場合。
        """
        with self._lock:
            if self._is_recording:
                raise RuntimeError("Recording has already started.")
            if self._is_stopping:
                raise RuntimeError("Recording is still stopping.")
            if self.has_pending_cleanup():
                raise RuntimeError("Previous recording cleanup has not finished.")

            self.error = None

        try:
            with self._lock:
                # targets からカメラの設定を抽出する。
                # カメラに保存形式が指定されていない、もしくは存在しないカメラの保存形式が指定された場合はエラー。
                camera_settings = {}
                for device_id, target in targets.items():
                    if target.type == "camera":
                        if target.video_options is None:
                            raise ValueError(f"Video format is required for camera {device_id}.")
                        camera_settings[device_id] = CameraSetting.model_validate(target.settings)
                    elif target.video_options is not None:
                        raise ValueError(f"Video options require a camera: {device_id}.")

                # 保存フォルダ名が指定されなければ現在時刻
                recording_name = name or datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
                if Path(recording_name).name != recording_name or recording_name in {".", ".."}:
                    raise ValueError("Recording name must be a single folder name.")
            
                # 親フォルダ、保存フォルダを作成。保存フォルダは新規作成のみ。上書きしない。
                parent_directory = Path(directory).expanduser()
                recording_directory = parent_directory / recording_name
                parent_directory.mkdir(parents=True, exist_ok=True)
                recording_directory.mkdir(exist_ok=False)

                # 変数の初期化
                self.directory = recording_directory    # 記録フォルダパス
                self.name = recording_name              # 記録フォルダ名
                self.metadata = {}                      # 今回の記録のメタデータを初期化する
                self._session_open = True
                self._stop_done = threading.Event()
                self._stop_error = None
                self._stop_metadata = None
                self._metadata_pending = False
                self._recording_stopped_at = None
                self._device_ids = frozenset(targets)

                # デバイスIDごとにファイルパスとCSVライター/ビデオレコーダーを作成
                used_names = set()
                for device_id, target in targets.items():
                    safe_id = re.sub(r"[^\w.-]+", "_", device_id).strip("._") or "device"
                    extension = target.video_options.extension if device_id in camera_settings else ".csv"
                    filename = f"{safe_id}{extension}"
                    suffix = 2
                    # 文字の置換やWindowsの大文字・小文字の同一視で衝突したら連番を付ける。
                    reserved_names = {filename.casefold()}
                    if device_id in camera_settings:
                        reserved_names.update((filename + suffix).casefold() for suffix in VideoRecorder.SIDECAR_SUFFIXES)
                    while reserved_names & used_names:
                        filename = f"{safe_id}_{suffix}{extension}"
                        suffix += 1
                        reserved_names = {filename.casefold()}
                        if device_id in camera_settings:
                            reserved_names.update((filename + ending).casefold() for ending in VideoRecorder.SIDECAR_SUFFIXES)
                    path = recording_directory / filename

                    # 動画のフレームデータの場合。レコーダーを作成・初期化して保持。
                    if device_id in camera_settings:
                        recorder = VideoRecorder(path, camera_settings[device_id], target.video_options)
                        self._video_recorders[device_id] = recorder
                        recorder.start()
                        used_names.update(reserved_names)
                        continue

                    # ファイルとライターを作成して保持。
                    # ライターの作成に失敗しても閉じられるよう、開いたファイルを先に登録する。
                    self._files[device_id] = path.open("x", newline="", encoding="utf-8-sig")
                    self._csv_writers[device_id] = csv.DictWriter(self._files[device_id], fieldnames=[])
                    used_names.add(filename.casefold())
                    self.logger.debug("Created CSV file: %s", path)

                self._is_recording = True
            
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            self.stop(error=exc)
            raise

    def write_metadata(self, metadata: dict) -> None:
        """作成済みの記録フォルダへメタデータをJSON形式で保存する。

        記録停止後も、終了日時を含む最終メタデータを保存できる。
        終了処理で収集した動画結果と確定済みの終了日時は、渡されたメタデータに結合する。

        Args:
            metadata (dict): メタデータ。記録設定やデバイスごとの設定を保持する。
        Raises:
            RuntimeError: 記録フォルダが未作成の場合。
            Exception: メタデータの保存に失敗した場合。
        """
        with self._lock:
            if self.directory is None:
                raise RuntimeError("Recording has not started.")

            temporary_path = None
            try:
                # 渡された辞書をコピーし、JSON文字列へ変換する。
                # 画像の保存結果"video_results"があれば、メタデータに追加する。
                saved_metadata = deepcopy(metadata)
                if "video_results" in self.metadata:
                    saved_metadata["video_results"] = deepcopy(self.metadata["video_results"])
                if self._recording_stopped_at is not None:
                    saved_metadata["recording_stopped_at"] = self._recording_stopped_at.isoformat()
                contents = json.dumps(saved_metadata, ensure_ascii=False, indent=4)
                metadata_path = Path(self.directory) / "metadata.json"

                # 同じ記録フォルダ内に一時ファイルを作成し、JSON全体を書き込む。
                with NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=self.directory,
                    prefix=".metadata-", suffix=".tmp", delete=False,   # withを抜けても一時ファイルは消されない
                ) as file:
                    temporary_path = Path(file.name)
                    file.write(contents)

                # 一時ファイルを古いmetadata.jsonと置き換える。
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
        """数値をCSVへ追記し、画像は対応するVideoRecorderへ渡す。

        保存先にはstart()で設定したself.directoryを使用する。
        start時の対象だけを保存し、初回に列名とヘッダーを確定する。
        """
        try:
            with self._lock:
                if not self._is_recording:
                    return
                if sample.id not in self._device_ids:
                    return

                if sample.id in self._video_recorders:
                    recorder = self._video_recorders[sample.id]
                    if sample.type != "frame":
                        raise ValueError("Camera recording requires frame data.")
                    if not isinstance(sample.data, CameraFrame):
                        raise TypeError("Frame data must contain CameraFrame.")

                    if recorder.write(sample.data, sample.timestamp_ns, elapsed_s) is False:
                        self.error = recorder.error
                    return

                if sample.type != "sample":
                    raise ValueError("Only sample data can be written to CSV.")
                device_id = sample.id

                # 共通の識別情報とdata内の測定値をCSVの列へ展開する。
                row = {
                    "id": device_id, 
                    "timestamp_ns": sample.timestamp_ns, 
                    **sample.data
                }
                if elapsed_s is not None:
                    row["elapsed_s"] = elapsed_s

                writer = self._csv_writers[device_id]
            
                # 初回の書き込み時に列名を確定し、ヘッダーを書き込む。
                if not writer.fieldnames:
                    writer.fieldnames = list(row)
                    writer.writeheader()

                writer.writerow(row)
                self.logger.debug("Writing sample: %s", sample)
            
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__
            self.stop(error=exc)
            raise

    def stop(self, metadata: dict | None = None, *, error: Exception | None = None) -> None:
        """受付を止め、動画・CSV・メタデータの終了処理を別スレッドで開始する。

        完了は待たない。呼び出し元で完了を確認する場合はwait_stopped()を使う。

        Args:
            metadata (dict | None): 停止命令までの測定情報。省略時は保持中の情報を使う。
            error (Exception | None): 記録を終了する原因となったエラー。
        """
        with self._lock:
            if self._is_stopping and not self._stop_done.is_set():
                if metadata is not None:
                    self._stop_metadata = deepcopy(metadata)
                if self._stop_error is None and error is not None:
                    self._stop_error = error
                return

            if not self._session_open and not self._metadata_pending:
                # 終了済みの記録へ再度停止を要求しても、ファイルは書き換えない。
                self._stop_error = error
                if error is not None:
                    self.error = str(error) or type(error).__name__
                return

            if metadata is not None:
                self._stop_metadata = deepcopy(metadata)
            self._is_recording = False
            self._stop_error = error
            self._stop_done = threading.Event()
            self._is_stopping = True        # 停止処理中
            recorders = tuple(self._video_recorders.values())
            self._finish_thread = threading.Thread(
                target=self._finalize_recording,
                name="recording-finalizer",
            )

        # 全カメラへ先に終了を要求する。動画のロック取得とjoinは保存状態のロック外で行う。
        for recorder in recorders:
            try:
                recorder.request_stop()
            except Exception as exc:
                with self._lock:
                    if self._stop_error is None:
                        self._stop_error = exc

        try:
            self._finish_thread.start()     # 停止監視スレッドを開始する
        except Exception as exc:
            with self._lock:
                self._finish_thread = None
                if self._stop_error is None:
                    self._stop_error = exc
                self.error = str(self._stop_error) or type(self._stop_error).__name__
                self._stop_done.set()

    def wait_stopped(self, timeout: float | None = None) -> None:
        """呼び出し元で終了処理の結果を待つ。共通Processorからは呼ばない。

        Args:
            timeout (float | None): 完了通知の待機上限秒。Noneなら完了まで待つ。

        Raises:
            Exception: 保存や終了処理に失敗した場合。動画が残る場合は停止を再試行できる。
        """
        # 最終JSONの書き込み中も、完了通知の待機上限を適用する。
        done = self._stop_done
        if not done.wait(timeout=timeout):
            raise TimeoutError("Timed out waiting for recording finalization.")

        with self._lock:
            error = self._stop_error
        if error is not None:
            raise error

    def is_finalizing(self) -> bool:
        """終了処理の実行中かを、ファイルI/Oのロックを待たずに返す。"""
        return self._is_stopping and not self._stop_done.is_set()

    def has_pending_cleanup(self) -> bool:
        """動作中の処理・未解放リソース・最終JSONの再保存が残っているかを返す。"""
        if not self._stop_done.is_set():
            return True
        with self._lock:
            return self._session_open or self._metadata_pending

    def _finalize_recording(self) -> None:
        """動画の終了を待ち、CSVと最終メタデータを保存して完了を通知する。"""
        with self._lock:
            recorders = tuple(self._video_recorders.items())
            files = tuple(self._files.items())
            error = self._stop_error

        try:
            results = {}        # キーはカメラID、値はVideoRecorder.get_result()の結果
            running = {}        # キーはカメラID、値は終了に失敗したVideoRecorder
            for device_id, recorder in recorders:
                try:
                    recorder.stop()     # FFmpegの終了を待つ
                except Exception as exc:
                    if error is None:
                        error = exc
                results[device_id] = recorder.get_result()
                if recorder.has_pending_cleanup():
                    running[device_id] = recorder

            # 一つの終了が失敗しても、残りの数値CSVを閉じる。
            unclosed = {}
            for device_id, file in files:
                try:
                    file.close()
                except Exception as exc:
                    if error is None:
                        error = exc
                if not file.closed:
                    unclosed[device_id] = file

            with self._lock:
                self._files = unclosed
                self._csv_writers = {
                    device_id: writer for device_id, writer in self._csv_writers.items() if device_id in unclosed
                }
                self._video_recorders = running     # 終了に失敗したVideoRecorderだけ残す
                self._device_ids = frozenset()
                self._session_open = bool(running or unclosed)
                if results:
                    self.metadata.setdefault("video_results", {}).update(results)

                if not self._session_open and self._recording_stopped_at is None:
                    self._recording_stopped_at = get_datetime()     # 記録停止時刻を確定する
                elif self._session_open and error is None:
                    error = TimeoutError("Recording resources have not been released.")

                # 停止時の測定情報へ動画結果と終了日時を結合し、APIへ完了を知らせる前に保存する。
                metadata = self.metadata if self._stop_metadata is None else self._stop_metadata
                self._metadata_pending = True
                self.write_metadata(metadata)
                self._metadata_pending = False

        except Exception as exc:
            if error is None:
                error = exc
        finally:
            with self._lock:
                # 待機中に渡された元の失敗理由を、終了処理中のエラーより優先する。
                self._stop_error = self._stop_error or error
                if self._stop_error is not None:
                    self.error = str(self._stop_error) or type(self._stop_error).__name__
                self._is_stopping = self._session_open
                self._finish_thread = None
                self._stop_done.set()

    def is_stopping(self) -> bool:
        """受付停止後の終了処理、またはタイムアウトした動画の再待機が必要かを返す。"""
        with self._lock:
            return self._is_stopping

    def get_recording_stopped_at(self, directory: str | None) -> datetime | None:
        """指定された記録フォルダと一致する場合だけ、確定した終了日時を返す。"""
        with self._lock:
            if directory is None or str(self.directory) != directory:
                return None
            return self._recording_stopped_at

    def is_recording(self) -> bool:
        """記録中かどうかを返す。"""
        with self._lock:
            return self._is_recording

    def is_recording_device(self, device_id: str) -> bool:
        """指定IDが現在の記録対象かどうかを返す。"""
        with self._lock:
            return self._is_recording and device_id in self._device_ids


class VideoRecorder:
    """カメラ1台のROIを上限付きQueueへコピーし、専用スレッドからFFmpegへ送る。"""

    # Queueの上限。画像32枚か1024 * 1024の画像64枚分のバイト数の小さいほうが上限。
    QUEUE_MAX_FRAMES = 32
    QUEUE_MAX_BYTES = 64 * 1024 * 1024
    STOP_TIMEOUT = 10.0
    SIDECAR_SUFFIXES = (".frames.csv", ".ffmpeg.log")

    def __init__(self, path: Path, settings: CameraSetting, options: VideoRecordingOptions):
        """出力先と、機器の現在値から独立した開始時の設定を保持する。

        Args:
            path (Path): 動画の出力先。
            settings (CameraSetting): 元画像の条件と保存ROI。
            options (VideoRecordingOptions): 動画の保存形式。
        """
        self.path = path                                    # 動画の出力先
        self.settings = settings.model_copy(deep=True)      # 元画像の条件と保存ROI
        self.options = options.model_copy(deep=True)        # 動画の保存形式

        # ROIが指定されていればROIのサイズ、なければ元画像のサイズを使用する。
        roi = self.settings.roi
        self.width  = roi.width if roi else self.settings.width
        self.height = roi.height if roi else self.settings.height

        # 1ピクセル当たりのバイト数。mono8なら1、RGBなら3。
        self.bytes_per_pixel = 1 if self.settings.pixel_format == "mono8" else 3

        # フレーム情報とFFmpegログの出力先
        self.frame_path = Path(str(path) + ".frames.csv")
        self.log_path   = Path(str(path) + ".ffmpeg.log")

        self.error = None
        self._state = "not_started"     # not_started, recording, completed, failed, empty
        self.accepted_frames = 0        # 保存queueに投入されたフレーム数
        self.written_frames = 0         # 動画とCSVに書き込まれたフレーム数

        # スレッドとイベント
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._accepting = False         # 新しい画像を受け付けるか。処理の終了とは区別する。
        self._thread = None
        self._ffmpeg_process = None
        self._resources = ExitStack()   # 開いたファイルの終了処理を後でまとめて実行する

    def start(self) -> None:
        """出力ファイルとFFmpegプロセス、保存スレッドを作成する。"""
        if self._thread is not None:
            raise RuntimeError("Video recorder has already started.")
        frame_bytes = self.width * self.height * self.bytes_per_pixel
        capacity = min(self.QUEUE_MAX_FRAMES, self.QUEUE_MAX_BYTES // frame_bytes)
        if capacity < 1:
            raise ValueError("A video frame exceeds the save queue memory limit.")
        executable = shutil.which("ffmpeg")
        if executable is None:
            raise FileNotFoundError("FFmpeg is required for video recording. Add ffmpeg.exe to PATH.")
        
        self._queue = queue.Queue(maxsize=capacity)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windowsで外部プロセスのコンソールウィンドウが表示されないようにする
        
        try:
            # 動画ファイル、フレーム対応CSV、FFmpegログのファイルを作成する。
            # ExitStack()を使用し、本来 with で行う終了処理を後でまとめて実行できるようにする。
            self._output_file = self._resources.enter_context(self.path.open("xb"))
            self._frame_file  = self._resources.enter_context(self.frame_path.open("x", newline="", encoding="utf-8-sig"))
            self._log_file    = self._resources.enter_context(self.log_path.open("xb"))

            self._csv_writer = csv.DictWriter(
                self._frame_file, fieldnames=[field.name for field in fields(VideoFrameRecord)],
            )
            self._csv_writer.writeheader()

            # FFmpegプロセスの起動
            command = self._build_ffmpeg_command(executable)
            self._ffmpeg_process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE, 
                stdout=self._output_file, 
                stderr=self._log_file,
                creationflags=flags,
            )

            self._state = "recording"
            self._accepting = True
            self._thread = threading.Thread(
                target=self._run, 
                name=f"video-recorder-{self.path.stem}"
                )
            self._thread.start()

        except Exception as exc:
            self._fail(exc)
            try:
                if self._ffmpeg_process is not None:
                    self._ffmpeg_process.kill()
                    self._ffmpeg_process.communicate(timeout=self.STOP_TIMEOUT)
            finally:
                self._resources.close()
                self._thread = None
            raise

    def _build_ffmpeg_command(self, executable: str) -> list[str]:
        """入力画像・圧縮方式・コンテナを指定したFFmpegコマンドを作成する。

        Args:
            executable (str): 使用するFFmpeg実行ファイルのパス。

        Returns:
            list[str]: subprocess.Popenへ渡す、実行ファイルを含む引数リスト。
        """
        monochrome = self.settings.pixel_format == "mono8"
        input_format = "gray" if monochrome else self.settings.pixel_format
        if self.options.format == "h264_lossless":
            # カラーはRGB用エンコーダーで保存し、非可逆のRGB→YUV変換を避ける。
            encoder = "libx264" if monochrome else "libx264rgb"
            output_format = "gray" if monochrome else "rgb24"
            codec = ["-c:v", encoder, "-preset", "veryfast", "-qp", "0", "-pix_fmt", output_format, "-color_range", "pc"]
        elif self.options.format == "ffv1":
            # FFV1 v3可逆。小さいROIでも行が欠けないよう1スライスとし、破損検出用CRCを付ける。
            # カラーはRGB成分を保持するbgr0形式を使う。入力のRGB/BGRの並びはFFmpegが変換する。
            output_format = "gray" if monochrome else "bgr0"
            codec = ["-c:v", "ffv1", "-level", "3", "-slices", "1", "-slicecrc", "1", "-pix_fmt", output_format]
        else:
            # MPEG-4非可逆。品質値2と、エンコーダーが対応するYUV画素形式を指定する。
            codec = ["-c:v", "mpeg4", "-vtag", "mp4v", "-q:v", "2", "-pix_fmt", "yuv420p"]

        # 可逆2方式はMKV。MP4は標準出力へ順次書き込めるフラグメント形式にする。
        container = ["-f", "matroska"]
        if self.options.format == "mpeg4":
            container = ["-f", "mp4", "-movflags", "frag_keyframe+empty_moov"]

        return [
            # 起動情報・進行表示を抑え、エラー時に終了する。標準入力からの対話操作は無効にする。
            executable, "-hide_banner", "-loglevel", "error", "-xerror", "-nostdin",
            # ROIの生画素列を、指定した画素形式・サイズ・FPSとして標準入力から読む。
            "-f", "rawvideo", "-pixel_format", input_format, "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.settings.fps), "-i", "pipe:0",
            # 音声なしで指定コーデックを使い、エンコーダーのスレッド数を1にする。
            "-an", *codec, "-threads", "1",
            # 指定コンテナの動画データを標準出力へ送る。Popen側で動画ファイルに接続する。
            *container, "pipe:1",
        ]

    def write(self, frame: CameraFrame, timestamp_ns: int, elapsed_s: float | None) -> bool:
        """ROIの画素をコピーしてQueueへ投入し、エンコードを待たずに戻る。

        Args:
            frame (CameraFrame): 元画像とフレーム情報。
            timestamp_ns (int): DeviceDataのPC側取得時刻 [ns]。
            elapsed_s (float | None): 共通の測定開始時刻からの経過秒。

        Returns:
            bool: 受付成功ならTrue。Queue上限到達や保存失敗で受付を停止した場合はFalse。
        """
        if self.error is not None:
            return False
        if not self._accepting:
            raise RuntimeError("Video recorder is not accepting frames.")

        # 画像形式の検証。
        image = memoryview(frame.image)     # メモリ上の画像のデータを参照する。コピーは行わない。
        expected_shape = (self.settings.height, self.settings.width)
        if self.bytes_per_pixel == 3:
            expected_shape += (3,)
        if image.format != "B" or image.shape != expected_shape:
            raise ValueError(f"Expected a {self.settings.pixel_format} uint8 image with shape {expected_shape}.")
        if not image.c_contiguous:  # メモリ配置が行順に連続であるかどうか
            raise ValueError("Camera image must be C-contiguous; copy the acquisition buffer first.")
        
        # 画像データを1次元のバイト列に変換する。
        flat = image.cast("B")
        roi = self.settings.roi
        if roi is None:
            pixels = flat.tobytes()
        else:
            # ROIの範囲をコピーして1次元のバイト列に変換する。
            pixels = b"".join(
                # 一次元バイト列から元画像の列に対応する部分を抜き出す。
                flat[(y * self.settings.width + roi.x) * self.bytes_per_pixel:
                     (y * self.settings.width + roi.x + roi.width) * self.bytes_per_pixel]
                # ROIの行範囲をループする。
                for y in range(roi.y, roi.y + roi.height)
            )

        with self._lock:
            if not self._accepting:
                return False
            try:
                self._queue.put_nowait((
                    pixels, 
                    frame.frame_index, 
                    timestamp_ns, 
                    frame.device_timestamp_ns, 
                    elapsed_s
                    ))
            except queue.Full:
                self._fail("Video save queue is full; camera recording stopped accepting frames.")
                return False
            self.accepted_frames += 1
        return True

    def _fail(self, error: object) -> None:
        """最初の失敗理由を保持し、新規受付を止める。呼び出し元が必要ならロックする。"""
        if self.error is None:
            self.error = str(error) or type(error).__name__
        self._accepting = False
        self._state = "failed"
        self._stop_event.set()

    def _write_frame(self, pixels: bytes) -> None:
        """保存スレッドから1フレームをFFmpegの標準入力へ送る。"""
        self._ffmpeg_process.stdin.write(pixels)

    def _run(self) -> None:
        """Queueを順に取り出し、動画とフレーム対応CSVを書き込む。"""
        try:
            # 終了要求後も、受付済みの画像が残っている間は処理を続ける。
            while not self._stop_event.is_set() or not self._queue.empty():
                # 画像の有無にかかわらず毎回 FFmpeg が終了していないか確認する。
                if self._ffmpeg_process.poll() is not None:
                    raise RuntimeError("FFmpeg exited before video recording stopped. See the ffmpeg.log file.")
                
                try:
                    pixels, index, timestamp, device_timestamp, elapsed = self._queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                try:
                    self._write_frame(pixels)
                    row = VideoFrameRecord(
                        video_frame_index=self.written_frames,
                        frame_index=index,
                        timestamp_ns=timestamp,
                        device_timestamp_ns=device_timestamp,
                        elapsed_s=elapsed,
                    )
                    self._csv_writer.writerow(asdict(row))
                    self.written_frames += 1

                finally:
                    self._queue.task_done()

            self._ffmpeg_process.stdin.close()
            code = self._ffmpeg_process.wait(timeout=self.STOP_TIMEOUT)

            # FFmpegの保存が正常に終了したかを検証。終了コードが0でない、もしくはログに出力があればエラーとする。
            diagnostic = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
            if code != 0 or diagnostic:
                raise RuntimeError(f"FFmpeg failed ({code}): {diagnostic[-4096:]}")

            # Queueに渡したフレーム数とFFmpegが書き込んだフレーム数を検証
            if self.accepted_frames != self.written_frames:
                raise RuntimeError("Accepted and written video frame counts differ.")
            
        except Exception as exc:
            with self._lock:
                self._fail(exc)
            
        finally:
            with self._lock:
                self._accepting = False
                
            try:
                # FFmpegの強制終了
                if self._ffmpeg_process.poll() is None:
                    self._ffmpeg_process.kill()
                    self._ffmpeg_process.wait(timeout=self.STOP_TIMEOUT)
                if not self._ffmpeg_process.stdin.closed:
                    self._ffmpeg_process.stdin.close()
            except Exception as exc:
                self._fail(exc)

            # Queueに残ったフレームの破棄。失敗時に未処理の画像参照を残さない。
            while True:
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except queue.Empty:
                    break

            try:
                self._frame_file.flush()
                self._output_file.flush()
            except Exception as exc:
                self._fail(exc)

            try:
                self._resources.close()
            except Exception as exc:
                self._fail(exc)
                
            with self._lock:
                self._state = "failed" if self.error else ("completed" if self.written_frames else "empty")

    def get_result(self) -> dict[str, str | None]:
        """現在の保存状態と最初のエラー理由を、新しい辞書で返す。

        Returns:
            dict[str, str | None]: stateとerror。最終結果はstop()の終了後に取得する。
        """
        with self._lock:
            return {"state": self._state, "error": self.error}

    def stop(self) -> None:
        """新規受付を止め、受付済みフレームと動画の終了処理を待つ。"""
        self.request_stop()

        if self._thread is not None:
            self._thread.join(timeout=self.STOP_TIMEOUT)

        # FFmpegへの書き込みが止まっている場合は、プロセスを終了させて待機を解く。
        if self.is_running():
            self._fail("Timed out waiting for video encoding to stop.")
            if self._ffmpeg_process is not None and self._ffmpeg_process.poll() is None:
                self._ffmpeg_process.kill()
            self._thread.join(timeout=self.STOP_TIMEOUT)

        if self.is_running():
            raise TimeoutError(self.error)
        if self._ffmpeg_process is not None and self._ffmpeg_process.poll() is None:
            self._ffmpeg_process.kill()
            self._ffmpeg_process.wait(timeout=self.STOP_TIMEOUT)

        close_error = None
        files = [getattr(self, name, None) for name in ("_frame_file", "_output_file", "_log_file")]
        if self._ffmpeg_process is not None:
            files.append(self._ffmpeg_process.stdin)
        for file in files:
            if file is not None and not file.closed:
                try:
                    file.close()
                except Exception as exc:
                    close_error = close_error or exc
                    self._fail(exc)
        if close_error is not None:
            raise close_error
        if self.error is not None:
            raise RuntimeError(self.error)

    def request_stop(self) -> None:
        """新しい画像の受付を止め、保存スレッドへ終了を要求する。完了は待たない。"""
        with self._lock:
            self._accepting = False
            self._stop_event.set()

    def is_running(self) -> bool:
        """動画保存スレッドが動作中かを返す。"""
        return self._thread is not None and self._thread.is_alive()

    def has_pending_cleanup(self) -> bool:
        """保存スレッド・FFmpeg・出力ファイルの終了が残っているかを返す。"""
        if self.is_running() or (self._ffmpeg_process is not None and self._ffmpeg_process.poll() is None):
            return True
        files = [getattr(self, name, None) for name in ("_frame_file", "_output_file", "_log_file")]
        if self._ffmpeg_process is not None:
            files.append(self._ffmpeg_process.stdin)
        return any(file is not None and not file.closed for file in files)
