"""数値CSV・動画・測定条件のJSONを、一つの記録フォルダへ保存する。

DataRecorderが機器IDごとの保存先を用意し、数値をCSVへ書き、画像をVideoRecorderへ渡す。
VideoRecorderは画像の必要範囲をコピーして専用Queueへ入れ、保存スレッドから
外部プログラムFFmpegへ送り、動画と各フレームの対応情報を記したCSVを作る。

記録終了では新規受付を止め、別スレッドで動画とCSVを閉じ、保存結果・終了日時を含む
metadata.jsonを確定する。受付停止、終了処理の完了、失敗後に残る後始末は区別して扱う。
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
from uuid import uuid4

from backend.app.models.devices import CameraSetting
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.models.recordings import (
    RecordingStatus, RecordingTarget, RecordingTargetStatus, VideoFrameRecord, VideoRecordingOptions,
)

from backend.app.utils.logging_utils import setup_logger
from backend.app.utils.time_utils import get_datetime

class DataRecorder:
    """記録対象と保存先を保持し、データの受付から保存終了までを管理する。

    startで対象機器ごとの保存処理を作り、writeで対象の数値・画像を受け付ける。
    数値は呼び出し元のスレッドでCSVへ書き、画像のエンコードは動画保存側へ任せる。
    機器の取得開始・停止は担当しない。

    stopは受付停止と終了処理の開始までを行う。呼び出し元はwait_stoppedで結果を待つ。
    失敗後は残ったファイル・動画処理・未保存JSONを保持し、stopで終了を再試行できる。
    後始末が残る間は次の記録を開始できない。
    記録ID・開始時の対象設定・保存結果は、次の記録フォルダを作成するまで残す。
    """
    def __init__(self, *, server_instance_id: str | None = None):
        """未記録の状態を用意する。起動IDを省略した単独利用では一度だけ発行する。"""
        self._lock = threading.RLock()  # 保存受付と終了処理の状態、メタデータの更新を同じロックで保護する。
        # 常にこのロックから_lockの順に取得する。JSON保存と記録の切り替えを直列化する。
        self._metadata_write_lock = threading.RLock()
        self.server_instance_id = server_instance_id if server_instance_id is not None else str(uuid4())
        self._recording_id: str | None = None
        self._measurement_id: str | None = None
        self._targets: dict[str, RecordingTargetStatus] = {}
        self._written_rows: dict[str, int] = {}
        self._is_recording = False
        self._is_stopping = False
        self._session_open = False      # 記録中、または動画・CSVの解放が残る間はTrue。
        self._finish_thread = None
        self._stop_done = threading.Event()
        self._stop_done.set()
        self._stop_error: Exception | None = None
        self._stop_metadata = None
        self._metadata_pending = False  # 保存リソースを閉じ終わっても、最終JSONの書き直しが残る場合がある。
        self._recording_stopped_at = None

        self.directory = None
        self.name = None
        self.error = None
        self.metadata = {}
        self._device_ids = frozenset()  # 記録開始時に固定した対象ID。後から取得を始めた機器は追加しない。
        self._files = {}
        self._csv_writers = {}
        self._video_recorders = {}

        self.logger = setup_logger(__class__.__name__)

    def get_status(self) -> RecordingStatus:
        """識別情報と対象の保存状態をコピーして返す。設定制限はMeasurementManagerが補う。

        Returns:
            RecordingStatus: 記録ID・開始時の対象情報・保存結果と、記録全体の状態。
        """
        with self._lock:
            for device_id, target in self._targets.items():
                recorder = self._video_recorders.get(device_id)
                if recorder is not None:
                    result = recorder.get_result()
                    if target.error is None:
                        target.state = result["state"]
                        if not self._is_recording and target.state == "recording":
                            target.state = "stopping"
                        target.error = result["error"]
                    if self.error is None and target.error is not None:
                        self.error = target.error

            return RecordingStatus(
                server_instance_id=self.server_instance_id,
                recording_id=self._recording_id,
                measurement_id=self._measurement_id,
                recording_stopped_at=self._recording_stopped_at,
                targets=deepcopy(self._targets),
                is_recording=self._is_recording,
                is_stopping=self._is_stopping,
                directory=str(self.directory) if self.directory is not None else None,
                name=self.name,
                error=self.error,
            )

    def start(
        self, directory: str, name: str | None = None, *, targets: dict[str, RecordingTarget],
        measurement_id: str | None = None,
    ) -> None:
        """新しい記録フォルダと、対象機器ごとのCSV・動画保存処理を作る。

        既存フォルダは再利用しない。開始途中で失敗した場合も作成済みの保存処理へ終了を要求する。

        Args:
            directory (str): 記録フォルダを作る親ディレクトリ。
            name (str | None): 一階層のフォルダ名。未指定または空文字なら現在時刻を使う。
            targets (dict[str, RecordingTarget]): 機器IDごとの種類・開始時設定・動画形式。
            measurement_id (str | None): この記録の経過時間の基準に対応する測定ID。

        Raises:
            RuntimeError: 記録中、または前回の後始末が残る場合。
            Exception: 対象設定の検証や保存先の作成に失敗した場合。
        """
        with self._metadata_write_lock:
            with self._lock:
                if self._is_recording:
                    raise RuntimeError("Recording has already started.")
                if self._is_stopping:
                    raise RuntimeError("Recording is still stopping.")
                # 未保存JSONだけが残る場合も、次の記録で状態を初期化しない。
                if self.has_pending_cleanup():
                    raise RuntimeError("Previous recording cleanup has not finished.")

                self.error = None

            preparing_device_id = None
            try:
                with self._lock:
                    camera_settings = {}
                    for device_id, target in targets.items():
                        if target.type == "camera":
                            if target.video_options is None:
                                raise ValueError(f"Video format is required for camera {device_id}.")
                            camera_settings[device_id] = CameraSetting.model_validate(target.settings)
                        elif target.video_options is not None:
                            raise ValueError(f"Video options require a camera: {device_id}.")

                    recording_name = name or datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
                    if Path(recording_name).name != recording_name or recording_name in {".", ".."}:
                        raise ValueError("Recording name must be a single folder name.")

                    parent_directory = Path(directory).expanduser()
                    recording_directory = parent_directory / recording_name
                    parent_directory.mkdir(parents=True, exist_ok=True)
                    recording_directory.mkdir(exist_ok=False)

                    self.directory = recording_directory
                    self.name = recording_name
                    self.metadata = {}
                    self._session_open = True
                    self._stop_done = threading.Event()
                    self._stop_error = None
                    self._stop_metadata = None
                    self._metadata_pending = False
                    self._recording_stopped_at = None
                    self._device_ids = frozenset(targets)
                    self._recording_id = str(uuid4())
                    self._measurement_id = measurement_id
                    self._targets = {
                        device_id: RecordingTargetStatus(
                            type=target.type, settings_at_start=deepcopy(target.settings),
                            video_options=target.video_options,
                        ) for device_id, target in targets.items()
                    }
                    self._written_rows = {device_id: 0 for device_id in targets if device_id not in camera_settings}

                    used_names = set()
                    for device_id, target in targets.items():
                        preparing_device_id = device_id
                        safe_id = re.sub(r"[^\w.-]+", "_", device_id).strip("._") or "device"
                        extension = target.video_options.extension if device_id in camera_settings else ".csv"
                        filename = f"{safe_id}{extension}"
                        suffix = 2
                        # 動画に付随するCSV・ログも予約し、Windowsの大文字小文字の同一視による衝突を避ける。
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

                        if device_id in camera_settings:
                            recorder = VideoRecorder(path, camera_settings[device_id], target.video_options)
                            self._video_recorders[device_id] = recorder
                            recorder.start()
                            self._targets[device_id].state = "recording"
                            used_names.update(reserved_names)
                            continue

                        # ライターの作成に失敗しても終了処理が閉じられるよう、ファイルを先に登録する。
                        self._files[device_id] = path.open("x", newline="", encoding="utf-8-sig")
                        self._csv_writers[device_id] = csv.DictWriter(self._files[device_id], fieldnames=[])
                        self._targets[device_id].state = "recording"
                        used_names.add(filename.casefold())
                        self.logger.debug("Created CSV file: %s", path)

                    self._is_recording = True

            except Exception as exc:
                with self._lock:
                    self.error = str(exc) or type(exc).__name__
                    if preparing_device_id is not None:
                        self._fail_target(preparing_device_id, exc)
                # 作成済みの保存処理へ終了を要求する。ここでは完了を待たず、元の例外を返す。
                self.stop(error=exc)
                raise

    def write_metadata(self, metadata: dict) -> None:
        """動画結果と終了日時を結合し、metadata.jsonを置き換える。

        記録停止後も呼べる。書き込みに失敗した場合は既存JSONを残し、例外を返す。

        Args:
            metadata (dict): 保存する測定条件と設定履歴。呼び出し元の辞書は変更しない。

        Raises:
            RuntimeError: 記録フォルダが作成されていない場合。
            Exception: JSON変換やファイルの書き込み・置換に失敗した場合。
        """
        with self._metadata_write_lock:
            temporary_path = None
            try:
                with self._lock:
                    if self.directory is None:
                        raise RuntimeError("Recording has not started.")
                    directory = self.directory
                    saved_metadata = deepcopy(metadata)
                    if "video_results" in self.metadata:
                        saved_metadata["video_results"] = deepcopy(self.metadata["video_results"])
                    if self._recording_stopped_at is not None:
                        saved_metadata["recording_stopped_at"] = self._recording_stopped_at.isoformat()

                # 状態GETはJSON変換やファイルI/Oを待たない。保存先の切り替えは外側のロックで止める。
                contents = json.dumps(saved_metadata, ensure_ascii=False, indent=4)
                metadata_path = Path(directory) / "metadata.json"

                # 書き込み途中で失敗しても既存JSONを残せるよう、同じフォルダの一時ファイルへ書く。
                with NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=directory,
                    prefix=".metadata-", suffix=".tmp", delete=False,
                ) as file:
                    temporary_path = Path(file.name)
                    file.write(contents)

                temporary_path.replace(metadata_path)
                with self._lock:
                    self.metadata = saved_metadata

            except Exception as exc:
                with self._lock:
                    self.error = str(exc) or type(exc).__name__
                raise
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def write(self, sample: DeviceData, elapsed_s: float | None = None) -> None:
        """対象機器の数値をCSVへ書き、画像は動画保存Queueへ渡す。

        受付停止中と対象外のデータは無視する。数値CSVの列は最初のサンプルで固定する。
        動画側の受付失敗は状態へ反映し、他機器の記録は続ける。例外が発生した場合は
        記録全体の終了を要求してから、その例外を呼び出し元へ返す。

        Args:
            sample (DeviceData): 機器IDとPC取得時刻を伴う数値サンプル、または画像フレーム。
            elapsed_s (float | None): 共通の取得開始からの経過秒。数値CSVではNoneなら列を省く。
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

                    # Falseはこのカメラの受付失敗。例外とは分けて、数値機器などの記録は継続する。
                    if recorder.write(sample.data, sample.timestamp_ns, elapsed_s) is False:
                        self.error = recorder.error
                    return

                if sample.type != "sample":
                    raise ValueError("Only sample data can be written to CSV.")
                device_id = sample.id

                row = {
                    "id": device_id,
                    "timestamp_ns": sample.timestamp_ns,
                    **sample.data
                }
                if elapsed_s is not None:
                    row["elapsed_s"] = elapsed_s

                writer = self._csv_writers[device_id]

                if not writer.fieldnames:
                    writer.fieldnames = list(row)
                    writer.writeheader()

                writer.writerow(row)
                self._written_rows[device_id] += 1
                self.logger.debug("Writing sample: %s", sample)

        except Exception as exc:
            with self._lock:
                self.error = str(exc) or type(exc).__name__
                self._fail_target(sample.id, exc)
            # 新規受付を止め、別スレッドで動画・CSV・最終JSONを終了する。
            self.stop(error=exc)
            raise

    def _fail_target(self, device_id: str, error: Exception) -> None:
        """対象の最初の失敗を残す。呼び出し元で_lockを保持する。"""
        target = self._targets.get(device_id)
        if target is not None:
            target.state = "failed"
            if target.error is None:
                target.error = str(error) or type(error).__name__

    def stop(self, metadata: dict | None = None, *, error: Exception | None = None) -> None:
        """保存受付を止め、動画・CSV・最終JSONの終了処理を別スレッドで開始する。

        完了はwait_stoppedで確認する。終了処理中の再要求ではメタデータと原因を補い、
        前回の試行が終わっていて後始末が残る場合は、その処理を再試行する。

        Args:
            metadata (dict | None): 停止命令までに集めた測定情報。Noneなら保持中の情報を使う。
            error (Exception | None): 記録終了の原因。通常停止ではNone。
        """
        with self._lock:
            if self._is_stopping and not self._stop_done.is_set():
                if metadata is not None:
                    self._stop_metadata = deepcopy(metadata)
                if self._stop_error is None and error is not None:
                    self._stop_error = error
                return

            if not self._session_open and not self._metadata_pending:
                # 後始末のない記録への再要求では、保存済みファイルを作り直さない。
                self._stop_error = error
                if error is not None:
                    self.error = str(error) or type(error).__name__
                return

            if metadata is not None:
                self._stop_metadata = deepcopy(metadata)
            self._is_recording = False
            self._stop_error = error
            # is_finalizingをロックなしで読む側が、前回の完了通知を参照しない順序にする。
            self._stop_done = threading.Event()
            self._is_stopping = True
            for target in self._targets.values():
                if target.state == "recording":
                    target.state = "stopping"
            recorders = tuple(self._video_recorders.values())
            self._finish_thread = threading.Thread(
                target=self._finalize_recording,
                name="recording-finalizer",
            )

        # 全カメラの受付を先に止める。動画側のロック取得は、保存状態のロックを解放してから行う。
        for recorder in recorders:
            try:
                recorder.request_stop()
            except Exception as exc:
                with self._lock:
                    if self._stop_error is None:
                        self._stop_error = exc

        try:
            self._finish_thread.start()
        except Exception as exc:
            with self._lock:
                self._finish_thread = None
                if self._stop_error is None:
                    self._stop_error = exc
                self.error = str(self._stop_error) or type(self._stop_error).__name__
                self._stop_done.set()

    def wait_stopped(self, timeout: float | None = None) -> None:
        """終了処理の結果を待つ。stopで終了を要求した後、共通Processor以外から呼ぶ。

        Args:
            timeout (float | None): 完了通知を待つ秒数。Noneなら期限を設けない。

        Raises:
            TimeoutError: 完了通知の待機期限に達した場合。終了処理は取り消さない。
            Exception: 記録や終了処理の失敗。後始末が残る場合はstopで再試行できる。
        """
        # 終了処理が状態を更新できるよう、完了通知は_lockを保持せずに待つ。
        done = self._stop_done
        if not done.wait(timeout=timeout):
            raise TimeoutError("Timed out waiting for recording finalization.")

        with self._lock:
            error = self._stop_error
        if error is not None:
            raise error

    def is_finalizing(self) -> bool:
        """終了スレッドの処理中かを、ファイル書き込みのロックを待たずに返す。"""
        return self._is_stopping and not self._stop_done.is_set()

    def has_pending_cleanup(self) -> bool:
        """動作中の終了処理、未解放リソース、未保存の最終JSONが残るかを返す。

        is_stoppingがFalseでも、最終JSONの再保存が必要ならTrueを返す。
        """
        if not self._stop_done.is_set():
            return True
        with self._lock:
            return self._session_open or self._metadata_pending

    def _finalize_recording(self) -> None:
        """終了スレッドで動画・CSVを閉じ、最終JSONを保存して結果を通知する。

        一部の終了失敗でも残りを処理し、未解放のものと未保存JSONは再試行用に残す。
        完了通知は試行が終わったことを表し、成功・失敗は_stop_errorで区別する。
        """
        with self._lock:
            recorders = tuple(self._video_recorders.items())
            files = tuple(self._files.items())
            error = self._stop_error

        try:
            results = {}
            running = {}
            for device_id, recorder in recorders:
                try:
                    recorder.stop()     # 受付済み画像の書き込みとFFmpegの終了を、この終了スレッドで待つ。
                except Exception as exc:
                    if error is None:
                        error = exc
                    with self._lock:
                        self._fail_target(device_id, exc)
                results[device_id] = recorder.get_result()
                with self._lock:
                    target = self._targets.get(device_id)
                    if target is not None and target.error is None:
                        target.state = results[device_id]["state"]
                        target.error = results[device_id]["error"]
                # スレッドが止まっても、プロセスやファイルが残れば後始末の対象に残す。
                if recorder.has_pending_cleanup():
                    running[device_id] = recorder

            unclosed = {}
            for device_id, file in files:
                try:
                    file.close()
                except Exception as exc:
                    if error is None:
                        error = exc
                    with self._lock:
                        self._fail_target(device_id, exc)
                if not file.closed:
                    unclosed[device_id] = file
                with self._lock:
                    target = self._targets[device_id]
                    if file.closed and target.state != "failed":
                        target.state = "completed" if self._written_rows[device_id] else "empty"

            # 先行するJSON保存が完了してから動画結果を結合し、古いコピーによる上書きを防ぐ。
            with self._metadata_write_lock:
                with self._lock:
                    self._files = unclosed
                    self._csv_writers = {
                        device_id: writer for device_id, writer in self._csv_writers.items() if device_id in unclosed
                    }
                    self._video_recorders = running     # スレッド停止済みでも、FFmpegやファイルが残る動画は再試行用に保持する。
                    self._device_ids = frozenset()
                    self._session_open = bool(running or unclosed)
                    if results:
                        self.metadata.setdefault("video_results", {}).update(results)

                    if not self._session_open and self._recording_stopped_at is None:
                        self._recording_stopped_at = get_datetime()
                    elif self._session_open and error is None:
                        error = TimeoutError("Recording resources have not been released.")

                    metadata = self.metadata if self._stop_metadata is None else self._stop_metadata
                    self._metadata_pending = True
                # 動画結果と終了日時を最終JSONへ反映する。I/O中は状態用ロックを解放する。
                self.write_metadata(metadata)
                with self._lock:
                    self._metadata_pending = False

        except Exception as exc:
            if error is None:
                error = exc
        finally:
            with self._lock:
                # 終了処理中に追加された原因も含め、最初の失敗を保持する。
                self._stop_error = self._stop_error or error
                if self._stop_error is not None:
                    self.error = str(self._stop_error) or type(self._stop_error).__name__
                self._is_stopping = self._session_open
                self._finish_thread = None
                # 成功通知ではなく試行の完了通知。待機側は_stop_errorも確認する。
                self._stop_done.set()

    def is_stopping(self) -> bool:
        """受付停止後の終了処理中、または未解放の保存リソースが残るかを返す。"""
        with self._lock:
            return self._is_stopping

    def get_recording_stopped_at(self, directory: str | None) -> datetime | None:
        """指定フォルダが今回の保存先と一致する場合だけ、確定した終了日時を返す。

        Args:
            directory (str | None): 終了日時を問い合わせる記録フォルダ。

        Returns:
            datetime | None: フォルダ不一致、未指定、または未確定ならNone。
        """
        with self._lock:
            if directory is None or str(self.directory) != directory:
                return None
            return self._recording_stopped_at

    def is_recording(self) -> bool:
        """新しい保存データを受け付けているかを返す。"""
        with self._lock:
            return self._is_recording

    def is_recording_device(self, device_id: str) -> bool:
        """指定IDが、現在受付中の記録の対象かを返す。"""
        with self._lock:
            return self._is_recording and device_id in self._device_ids


class VideoRecorder:
    """カメラ一台の画像を、上限付きQueueと専用スレッドで動画へ保存する。

    開始時の設定と保存形式を保持し、画像全体またはROI（切り出し範囲）を保存する。
    writeは画素をコピーして戻るため、呼び出し元は取得バッファを再利用できる。
    動画とは別に、保存順と取得時刻を対応させるCSV、およびFFmpegの診断ログを作る。

    request_stopは受付停止のみ、stopは受付済み画像とファイル・FFmpegの終了までを扱う。
    Queue満杯や保存失敗ではこのカメラの受付を止め、最初の失敗理由を保持する。
    未解放リソースがあればstopを再試行できる。
    """

    # 画像枚数と、保存する画素の合計64MiBのうち、先に達する方でQueueを制限する。
    QUEUE_MAX_FRAMES = 32
    QUEUE_MAX_BYTES = 64 * 1024 * 1024
    STOP_TIMEOUT = 10.0
    SIDECAR_SUFFIXES = (".frames.csv", ".ffmpeg.log")

    def __init__(self, path: Path, settings: CameraSetting, options: VideoRecordingOptions):
        """保存条件をコピーして保持し、画像の切り出し後の寸法と出力先を決める。

        Args:
            path (Path): 動画の出力先。対応CSVとログは同じパスへ接尾辞を付けて作る。
            settings (CameraSetting): 元画像の寸法・画素形式・FPSと、任意の切り出し範囲。
            options (VideoRecordingOptions): 動画の圧縮形式。
        """
        self.path = path
        self.settings = settings.model_copy(deep=True)
        self.options = options.model_copy(deep=True)

        roi = self.settings.roi
        self.width  = roi.width if roi else self.settings.width
        self.height = roi.height if roi else self.settings.height

        self.bytes_per_pixel = 1 if self.settings.pixel_format == "mono8" else 3

        self.frame_path = Path(str(path) + ".frames.csv")
        self.log_path   = Path(str(path) + ".ffmpeg.log")

        self.error = None
        self._state = "not_started"
        self.accepted_frames = 0        # Queueへ受け付けた枚数。書き込み完了の枚数とは区別する。
        self.written_frames = 0         # FFmpegへの画素送信と、対応CSVの追記が済んだ枚数。

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._accepting = False         # 受付停止後も保存スレッドの処理やファイル解放が残る。
        self._thread = None
        self._ffmpeg_process = None
        self._resources = ExitStack()

    def start(self) -> None:
        """出力ファイルとFFmpegを用意し、画像を保存するスレッドを起動する。

        Queueの容量は画像枚数と画素のバイト数で制限する。出力ファイルは新規作成のみ。

        Raises:
            RuntimeError: すでに保存スレッドを作成している場合。
            ValueError: 一枚の画像がQueueのバイト数上限を超える場合。
            FileNotFoundError: FFmpegがPATHにない場合。
            Exception: ファイル作成やプロセス・スレッドの起動に失敗した場合。
        """
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
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # WindowsではFFmpegのコンソールウィンドウを表示しない。

        try:
            # 開始途中の失敗と保存スレッドの終了時に、同じファイル群をまとめて閉じる。
            self._output_file = self._resources.enter_context(self.path.open("xb"))
            self._frame_file  = self._resources.enter_context(self.frame_path.open("x", newline="", encoding="utf-8-sig"))
            self._log_file    = self._resources.enter_context(self.log_path.open("xb"))

            self._csv_writer = csv.DictWriter(
                self._frame_file, fieldnames=[field.name for field in fields(VideoFrameRecord)],
            )
            self._csv_writer.writeheader()

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
        """画素の入力条件・圧縮形式・出力コンテナを指定する引数を作る。

        Args:
            executable (str): 使用するFFmpeg実行ファイルのパス。

        Returns:
            list[str]: Popenへ渡す、実行ファイルを含むコマンド引数。
        """
        monochrome = self.settings.pixel_format == "mono8"
        input_format = "gray" if monochrome else self.settings.pixel_format
        if self.options.format == "h264_lossless":
            # カラーの可逆保存ではRGB用エンコーダーを使い、RGBからYUVへの変換を避ける。
            encoder = "libx264" if monochrome else "libx264rgb"
            output_format = "gray" if monochrome else "rgb24"
            codec = ["-c:v", encoder, "-preset", "veryfast", "-qp", "0", "-pix_fmt", output_format, "-color_range", "pc"]
        elif self.options.format == "ffv1":
            # 小ROIは1スライスで画素欠落を避け、FFmpeg 7.0.2が1を拒否する寸法では自動選択する。
            slices = "1" if self.width <= 352 and self.height <= 288 else "0"
            # カラーはRGB成分を保持するbgr0で出力し、入力RGB/BGRの並び替えはFFmpegへ任せる。
            output_format = "gray" if monochrome else "bgr0"
            codec = ["-c:v", "ffv1", "-level", "3", "-slices", slices, "-slicecrc", "1", "-pix_fmt", output_format]
        else:
            # MPEG-4は非可逆保存として、エンコーダーに対応するYUV形式を使う。
            codec = ["-c:v", "mpeg4", "-vtag", "mp4v", "-q:v", "2", "-pix_fmt", "yuv420p"]

        # 標準出力はseekできないため、MP4には順次書き込めるフラグメント形式を指定する。
        container = ["-f", "matroska"]
        if self.options.format == "mpeg4":
            container = ["-f", "mp4", "-movflags", "frag_keyframe+empty_moov"]

        return [
            executable, "-hide_banner", "-loglevel", "error", "-xerror", "-nostdin",
            # ROIを切り出した後の画素列として、寸法・形式・FPSをFFmpegへ伝える。
            "-f", "rawvideo", "-pixel_format", input_format, "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.settings.fps), "-i", "pipe:0",
            "-an", *codec, "-threads", "1",
            # 動画の標準出力はPopenでファイルへ接続する。Python側で動画全体を保持しない。
            *container, "pipe:1",
        ]

    def write(self, frame: CameraFrame, timestamp_ns: int, elapsed_s: float | None) -> bool:
        """保存する画素をコピーし、Queueの空きを待たずに投入する。

        Args:
            frame (CameraFrame): 開始時の寸法・画素形式に合う、メモリ配置が連続した画像。
            timestamp_ns (int): PC側の取得カウンタ時刻。単位はns。
            elapsed_s (float | None): 共通の取得開始からの経過秒。

        Returns:
            bool: Queueへの受付成功ならTrue。保存失敗・満杯・コピー中の受付停止ではFalse。

        Raises:
            RuntimeError: エラーを伴わず受付停止済みの場合。
            ValueError: 画像の形式・寸法・メモリ配置が保存条件と合わない場合。
        """
        if self.error is not None:
            return False
        if not self._accepting:
            raise RuntimeError("Video recorder is not accepting frames.")

        image = memoryview(frame.image)
        expected_shape = (self.settings.height, self.settings.width)
        if self.bytes_per_pixel == 3:
            expected_shape += (3,)
        if image.format != "B" or image.shape != expected_shape:
            raise ValueError(f"Expected a {self.settings.pixel_format} uint8 image with shape {expected_shape}.")
        if not image.c_contiguous:
            raise ValueError("Camera image must be C-contiguous; copy the acquisition buffer first.")

        flat = image.cast("B")
        roi = self.settings.roi
        # 取得バッファが再利用されても保存画像が変わらないよう、独立したbytesにコピーする。
        if roi is None:
            pixels = flat.tobytes()
        else:
            pixels = b"".join(
                flat[(y * self.settings.width + roi.x) * self.bytes_per_pixel:
                     (y * self.settings.width + roi.x + roi.width) * self.bytes_per_pixel]
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
        """最初の失敗理由を保持し、新規受付を止めて保存スレッドへ終了を要求する。

        この関数ではロックを取得しない。共有状態を変更する呼び出し元で必要な排他を行う。

        Args:
            error (object): 文字列化して保持する失敗理由。
        """
        if self.error is None:
            self.error = str(error) or type(error).__name__
        self._accepting = False
        self._state = "failed"
        self._stop_event.set()

    def _write_frame(self, pixels: bytes) -> None:
        """保存スレッドから、一枚分の画素をFFmpegの標準入力へ書く。"""
        self._ffmpeg_process.stdin.write(pixels)

    def _run(self) -> None:
        """受付済み画像をFFmpegへ送り、取得情報を対応CSVへ書く。

        停止要求後もQueueを処理し、入力を閉じてFFmpegの終了を待つ。
        失敗時は理由を保持し、残った画像を破棄してプロセスとファイルを終了する。
        終了に失敗したリソースは、外部からのstopで再処理できる。
        """
        try:
            # 停止要求の前に受け付けた画像は、正常時はすべて書いてから終了する。
            while not self._stop_event.is_set() or not self._queue.empty():
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

            # エラーログのみを出す設定なので、終了コード0でも診断出力があれば失敗として扱う。
            diagnostic = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
            if code != 0 or diagnostic:
                raise RuntimeError(f"FFmpeg failed ({code}): {diagnostic[-4096:]}")

            # ここで比べるのは受付枚数と画素送信・CSV追記済み枚数。FFmpegの内部フレーム数ではない。
            if self.accepted_frames != self.written_frames:
                raise RuntimeError("Accepted and written video frame counts differ.")

        except Exception as exc:
            with self._lock:
                self._fail(exc)

        finally:
            with self._lock:
                self._accepting = False

            try:
                if self._ffmpeg_process.poll() is None:
                    self._ffmpeg_process.kill()
                    self._ffmpeg_process.wait(timeout=self.STOP_TIMEOUT)
                if not self._ffmpeg_process.stdin.closed:
                    self._ffmpeg_process.stdin.close()
            except Exception as exc:
                self._fail(exc)

            # 失敗で保存できなかった画像の参照を破棄し、Queueの完了待ちも解除する。
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
        """保存状態と最初の失敗理由を、新しい辞書として返す。

        Returns:
            dict[str, str | None]: stateとerror。最終結果はstopの終了後に確認する。
        """
        with self._lock:
            return {"state": self._state, "error": self.error}

    def stop(self) -> None:
        """画像受付を止め、保存スレッド・FFmpeg・ファイルの終了を確認する。

        保存スレッドが応答しない場合はFFmpegを終了させて再度待つ。未解放のファイルも
        閉じ、残ったリソースは次の呼び出しで再試行できるよう参照を保持する。

        Raises:
            TimeoutError: 保存スレッドの終了を確認できない場合。
            Exception: プロセス・ファイルの終了失敗、または保持していた保存エラー。
        """
        # request_stopは受付と終了フラグを変更するだけで、残り画像の書き込みは待たない。
        self.request_stop()

        if self._thread is not None:
            self._thread.join(timeout=self.STOP_TIMEOUT)

        # 標準入力への書き込みで詰まった保存スレッドを、FFmpegの終了によって解放する。
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
        """画像受付を止め、受付済みの画像を処理して終了するよう要求する。完了は待たない。"""
        with self._lock:
            self._accepting = False
            if self._state == "recording":
                self._state = "stopping"
            self._stop_event.set()

    def is_running(self) -> bool:
        """動画保存スレッドが動作中かを返す。"""
        return self._thread is not None and self._thread.is_alive()

    def has_pending_cleanup(self) -> bool:
        """保存スレッド・FFmpeg・未解放ファイルが残るかを返す。"""
        if self.is_running() or (self._ffmpeg_process is not None and self._ffmpeg_process.poll() is None):
            return True
        files = [getattr(self, name, None) for name in ("_frame_file", "_output_file", "_log_file")]
        if self._ffmpeg_process is not None:
            files.append(self._ffmpeg_process.stdin)
        return any(file is not None and not file.closed for file in files)
