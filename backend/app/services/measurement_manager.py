""" 
複数デバイスの別スレッドでのデータ取得や保存、解析、
websocket通信などを管理するクラス。

非同期処理自体はdriver側で行う。
MeasurementManagerでは、それらの非同期処理の管理、
全データをself.data_queueに集約し、保存・解析・websocket送信などを行う。

self.data_queue のデータを処理するProcessorと、件数を監視するスレッドがある。
start_recording()が実行されるとデータをDataRecorder()クラスに渡す。
DataRecorder()は、このprocessorスレッドから呼び出されてファイルへ書き込む。
"""

import queue
import threading
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from backend.app.models.events import MeasurementEvent
from backend.app.models.devices import CameraSetting
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.models.recordings import RecordingTarget, VideoRecordingOptions
from backend.app.services.data_racorder import DataRecorder
from backend.app.services.camera_preview import CameraPreviewService

from backend.app.utils.time_utils import (
    get_counter_ns,
    get_datetime,
    counter_to_elapsed_seconds,
    counter_to_datetime,
)
from backend.app.utils.logging_utils import setup_logger


class RecordingSettingsConflictError(RuntimeError):
    """記録中のカメラに対して、固定した撮影条件の変更が要求された。"""


@dataclass
class QueueError:
    """共通Queueの検出情報と、その停止処理の失敗理由を保持する。"""
    details: dict[str, object]  # 検出時点の情報。生成後は変更しない。
    stop_error: str | None = None


@dataclass
class RecordingCommand:
    """Queueに渡す記録開始・終了命令。"""
    action: str                         # start / stop : 記録の開始・終了
    directory: str | None = None        # 保存先フォルダ
    name: str | None = None             # 保存フォルダ名
    device_ids: tuple[str, ...] = ()    # 記録開始命令を送った時点で取得中のデバイスID
    video_options: dict[str, VideoRecordingOptions] = field(default_factory=dict)       # カメラIDごとの保存形式
    done: threading.Event = field(default_factory=threading.Event)  # Processorでの命令処理完了。保存終了とは別。
    error: Exception | None = None      # エラーを格納


@dataclass
class Metadata:
    """測定のメタデータ。記録設定やデバイスごとの設定を保持する。"""
    name: str | None = None     # 保存フォルダ名
    path: str | None = None     # 保存フォルダパス
    measurement_started_at: datetime | None = None   # elapsed_sの基準となる測定開始日時
    measurement_start_counter_ns: int | None = None  # elapsed_sの基準カウンタ [ns]
    recording_started_at: datetime | None = None     # 記録開始日時
    recording_stopped_at: datetime | None = None     # 記録終了日時
    # キーはデバイスID、値は時刻・info・settingsを格納する履歴のリスト。
    devices: dict[str, list[dict[str, object]]] = field(default_factory=dict)       # デバイスごとの設定履歴。
    video_options: dict[str, dict[str, str]] = field(default_factory=dict)          # カメラIDごとの保存形式の履歴。DataRecorder側で保存する。
    acquisition_errors: list[dict[str, object]] = field(default_factory=list)       # 取得中のQueue件数超過や投入失敗などのエラー履歴。


class MeasurementManager:
    """
    複数デバイスのデータ取得や保存、解析、
    websocket通信などを管理するクラス。
    """

    # 共通処理スレッドの終了待機時間
    STOP_TIMEOUT = 2.0
    QUEUE_MAXSIZE = 350
    QUEUE_STOP_THRESHOLD = 300
    QUEUE_MONITOR_INTERVAL = 0.1
    COMMAND_PUT_TIMEOUT = 1.0
    SHUTDOWN_WAIT_TIMEOUT = 10.0

    def __init__(
        self, device_manager, event_broadcaster, camera_preview_service: CameraPreviewService | None = None,
    ) -> None:
        self.device_manager    = device_manager
        self.event_broadcaster = event_broadcaster
        self.camera_preview_service = camera_preview_service

        # threading関連
        self.data_queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self.latest_settings: dict[str, DeviceData] = {}  # 記録開始時の初期状態に使う最新の設定通知
        self._processor_thread = None               # 共通データ処理スレッド
        self._state_lock = threading.RLock()        # APIからの開始・停止処理を同期する
        self._recording_lock = threading.Lock()     # 記録の開始・終了と設定更新を直列化する。
        self._metadata_lock = threading.Lock()      # メタデータの読み取りと履歴への追記を同期する
        self._shutdown_requested = threading.Event()
        self._shutdown_lock = threading.Lock()
        # 取得側の通知では、取得スレッドの終了待機に使う_state_lockを取得しない。
        self._queue_error_lock = threading.Lock()
        self._last_queue_error: QueueError | None = None
        # エラー内容は複製せず、Processorが保存するまで同じ情報への参照を残す。
        self._queue_errors_pending_metadata: list[QueueError] = []
        self._queue_stop_requested = False
        self._queue_stop_attempted = False  # 停止処理が失敗した場合も試行後はTrue。
        self._queue_monitor_wakeup = threading.Event()
        self._queue_monitor_shutdown = threading.Event()
        self._queue_monitor_thread: threading.Thread | None = None
    
        # Processorへの終了シグナル。
        self._stop_signal = object()                # Processor終了

        # DataRecorderの初期化
        self._data_recorder = DataRecorder()

        # メタデータの初期化
        self._metadata = Metadata()

        # サンプリング開始時刻。elapsed_sの基準になる。
        self._start_counter_ns: int | None = None
        self._start_datetime = None

        self.logger = setup_logger(__class__.__name__)

        # 共通データ処理と件数監視を開始する。
        self._start_processor()
        self._queue_monitor_thread = threading.Thread(
            target=self._queue_monitor_loop, name="measurement-queue-monitor",
        )
        self._queue_monitor_thread.start()

    # ========================== デバイス設定管理 ==========================

    def get_settings(self, device_id: str) -> BaseModel:
        """指定した機器の適用済み設定を取得する。

        Args:
            device_id (str): 設定を取得する機器のID。

        Returns:
            BaseModel: ドライバーが保持する機器固有の設定。
        """
        device = self.device_manager.get(device_id)
        return device.get_settings()

    def set_settings(self, device_id: str, settings: dict[str, Any] | BaseModel) -> BaseModel:
        """記録中のFPS・ROI変更を拒否し、許可した設定をドライバーへ渡す。

        Args:
            device_id (str): 設定を更新する機器のID。
            settings (dict[str, Any] | BaseModel): 機器固有の設定一式。

        Returns:
            BaseModel: ドライバーが返した適用済みの設定。

        Raises:
            RecordingSettingsConflictError: 記録対象カメラのFPS・ROIを変更する場合。
        """
        with self._recording_lock:
            self._ensure_not_shutting_down()
            device = self.device_manager.get(device_id)

            # デバイスがカメラの場合、記録中のFPS・ROI変更を拒否する
            if self._data_recorder.is_recording_device(device_id) and device.get_info().type == "camera":
                values = settings.model_dump() if isinstance(settings, BaseModel) else settings
                requested = CameraSetting.model_validate(values)
                with self._metadata_lock:
                    recorded = CameraSetting.model_validate(self._metadata.devices[device_id][0]["settings"])
                if requested.fps != recorded.fps or requested.roi != recorded.roi:
                    raise RecordingSettingsConflictError("Cannot change FPS or ROI while recording this camera.")
                
            return device.set_settings(settings)

    # ========================== metadata 管理 ==========================

    def get_metadata(self) -> Metadata:
        """保持している記録情報とデバイス履歴のコピーを返す。"""
        with self._metadata_lock:
            metadata = deepcopy(self._metadata)
        stopped_at = self._data_recorder.get_recording_stopped_at(metadata.path)
        if stopped_at is not None:
            metadata.recording_stopped_at = stopped_at
        return metadata

    def _add_device_metadata(
        self,
        device_id: str,
        timestamp_ns: int,
        info: dict[str, object] | None = None,
        settings: dict[str, object] | None = None,
    ) -> None:
        """指定時刻の情報・設定をコピーし、デバイスの履歴へ追記する。"""
        try:
            # 引数に含まれていない情報・設定だけをデバイスから取得する。
            if info is None or settings is None:
                device = self.device_manager.get(device_id)
                if info is None:
                    info = device.get_info().model_dump(mode="json")
                if settings is None:
                    settings = device.get_settings().model_dump(mode="json")

            entry = {
                "timestamp_ns": timestamp_ns,
                "info": deepcopy(info),
                "settings": deepcopy(settings),
            }
            with self._metadata_lock:
                # devicesの履歴リストが存在しない場合は新規作成し、履歴を追記する。
                self._metadata.devices.setdefault(device_id, []).append(entry)
        except Exception as exc:
            raise RuntimeError(f"Could not collect metadata for device {device_id}: {exc}") from exc

    def _initialize_metadata(self, 
                             device_ids: tuple[str, ...], 
                             video_options: dict[str, VideoRecordingOptions]
                             ) -> None:
        """
        metadataを初期化する。
        今回の記録情報と、対象デバイスの初期設定をメタデータに格納する。
        """
        status = self._data_recorder.get_status()
        with self._metadata_lock:
            # 保存先と時刻を設定し、前回の記録の履歴を初期化する。
            self._metadata = Metadata(
                name=status.name,
                path=status.directory,
                measurement_started_at=self._start_datetime,
                measurement_start_counter_ns=self._start_counter_ns,
                recording_started_at=get_datetime(),
                video_options={key: value.model_dump(mode="json") 
                               for key, value in video_options.items()
                               },
            )

        # デバイスの設定情報の追加。開始命令より前に受信した最新の設定通知を、初期状態として追加する。
        for device_id in device_ids:
            notification = self.latest_settings.get(device_id)
            if notification is None:
                raise RuntimeError(f"No settings received for device {device_id}.")
            self._add_device_metadata(
                device_id, notification.timestamp_ns, settings=notification.data,
            )

    def _metadata_dict(self) -> dict:
        """測定情報と設定履歴のコピーをJSON対応の辞書へ変換する。"""
        with self._metadata_lock:
            metadata = asdict(self._metadata)

        # 日時をタイムゾーン付きのISO形式へ変換し、未設定の値はNoneのまま渡す。
        for key, value in metadata.items():
            if isinstance(value, datetime):
                metadata[key] = value.isoformat()
        return metadata

    def _save_metadata(self) -> None:
        """測定情報と設定履歴をDataRecorderへ渡し、保存する。"""
        self._data_recorder.write_metadata(self._metadata_dict())

    # ========================== Sampling ==========================
    # デバイスのデータ取得を開始・停止する。
    #
    # ここでは、デバイスへ共通データQueueを渡してデータ取得を開始・停止の命令を送るだけ 
    #   データの非同期取得は device クラス 
    #   Queueデータの処理は processor スレッド
    #   にそれぞれ委譲する。
    # 
    # サンプリングを追加しただけでは、データは保存されない。
    # 保存するには、サンプリングデバイスを追加後にdata_recorder.start_recording()を呼び出す必要がある。

    def start_sampling(self, device_id: str) -> None:
        """指定されたデバイスのデータ取得を開始する。"""
        with self._state_lock:
            self._ensure_not_shutting_down()
            device = self.device_manager.get(device_id)

            with self._queue_error_lock:
                stop_requested = self._queue_stop_requested
                stop_attempted = self._queue_stop_attempted
            if stop_requested and (not stop_attempted or self._is_any_device_sampling()):
                raise RuntimeError("Queue overflow stop has not completed. Stop all sampling before restarting.")

            if device.is_sampling():
                return
            self._start_processor()     # Processorが停止していた場合は再起動する

            # 最初のデバイスを開始時。共通のサンプリング開始時刻を設定する
            if not self._is_any_device_sampling():
                # 前回測定のデータが残っている場合は処理完了を待つ
                self.data_queue.join()
                with self._queue_error_lock:
                    self._queue_stop_requested = False
                    self._queue_stop_attempted = False
                    if not self._data_recorder.is_recording() and not self._data_recorder.is_stopping():
                        self._last_queue_error = None
                if not self._data_recorder.is_recording() and not self._data_recorder.is_stopping():
                    self._start_counter_ns = get_counter_ns()
                    self._start_datetime = get_datetime()
    
            try:
                # デバイスがカメラの場合はプレビューを開始する
                if self.camera_preview_service is not None and device.get_info().type == "camera":
                    self.camera_preview_service.start_camera(device_id)

                device.start_sampling(self.data_queue, on_queue_full=self._request_queue_stop)

            except Exception:
                if self.camera_preview_service is not None:
                    self.camera_preview_service.stop_camera(device_id)

                # 最初のデバイスの開始に失敗した場合は基準時刻を破棄する
                if not self._is_any_device_sampling() and not self._data_recorder.is_recording() and not self._data_recorder.is_stopping():
                    self._start_counter_ns = None
                    self._start_datetime = None

                self.logger.exception("Could not start sampling for device %s", device_id)
                raise

    def stop_sampling(self, device_id: str) -> None:
        """指定されたデバイスのデータ取得を停止する。"""
        with self._state_lock:
            device = self.device_manager.get(device_id)
            if self.camera_preview_service is not None:
                self.camera_preview_service.stop_camera(device_id)  # カメラのプレビュー解除
            if not device.is_sampling():
                return
            
            device.stop_sampling()

    def stop_all_sampling(self) -> None:
        """登録されている全てのデバイスのデータ取得を停止する。"""
        with self._state_lock:
            errors = []

            for device_id in list(self.device_manager.get_all().keys()):
                try:
                    self.stop_sampling(device_id)
                except Exception as exc:
                    errors.append((device_id, exc))
                    self.logger.exception("Could not stop sampling for device %s", device_id)
            if errors:
                raise RuntimeError("Could not stop all sampling devices.")

    def is_sampling(self, device_id: str) -> bool:
        """指定されたデバイスがデータ取得中かどうかを返す。"""
        with self._state_lock:
            device = self.device_manager.get(device_id)
            return device.is_sampling()

    def _is_any_device_sampling(self) -> bool:
        """いずれかのデバイスがデータ取得中かどうかを返す。"""
        for device in self.device_manager.get_all().values():
            if device.is_sampling():
                return True

        return False

    # ========================== Queue件数監視 ==========================

    def _request_queue_stop(
        self, device_id: str | None = None, *, reason: str = "queue_full", queue_size: int | None = None,
    ) -> None:
        """停止理由を保持して監視スレッドを起こす。呼び出し元では停止・I/O待機をしない。

        Args:
            device_id (str | None): 投入に失敗した機器ID。管理側の検出ではNone。
            reason (str): 投入失敗はqueue_full、件数超過はqueue_threshold_exceeded。
            queue_size (int | None): 件数超過を検出した時点の件数。省略時は現在の件数。
        """
        with self._queue_error_lock:    # 複数の機器から停止通知されても重ならないようにする
            if self._queue_stop_requested:
                return
            
            count = self.data_queue.qsize() if queue_size is None else queue_size
            message = (
                f"Acquisition queue exceeded {self.QUEUE_STOP_THRESHOLD} items ({count} queued)."
                if reason == "queue_threshold_exceeded"
                else f"Acquisition queue is full; data could not be queued (device={device_id})."
            )
            error = QueueError(details={
                "reason": reason, 
                "device_id": device_id, 
                "queue_size": count,
                "detected_at": get_datetime().isoformat(), 
                "message": message,
            })
            self._last_queue_error = error
            self._queue_errors_pending_metadata.append(error)
            self._queue_stop_requested = True

        self._queue_monitor_wakeup.set()        # 件数監視スレッドに通知

    def _queue_monitor_loop(self) -> None:
        """件数超過と投入失敗を監視し、同じ取得期間では一度だけ全取得を停止する。"""
        while not self._queue_monitor_shutdown.is_set():
            # 取得側の停止要求を調べる
            self._queue_monitor_wakeup.wait(timeout=self.QUEUE_MONITOR_INTERVAL)
            self._queue_monitor_wakeup.clear()
            if self._queue_monitor_shutdown.is_set():
                break
                
            # 件数を確認
            count = self.data_queue.qsize()
            if count > self.QUEUE_STOP_THRESHOLD:
                self._request_queue_stop(reason="queue_threshold_exceeded", queue_size=count)   # エラーメッセージを保持するため

            # 停止の判断。初めの一度だけ全取得を停止する。
            with self._queue_error_lock:
                should_stop = self._queue_stop_requested and not self._queue_stop_attempted
            if not should_stop:
                continue

            try:
                self.stop_all_sampling()
            except Exception as exc:
                with self._queue_error_lock:
                    self._last_queue_error.stop_error = str(exc)
                self.logger.exception("Could not stop sampling after acquisition queue overflow")
            finally:
                with self._queue_error_lock:
                    self._queue_stop_attempted = True

    def _save_queue_error_metadata(self) -> None:
        """検出済みの取得エラーを、記録中の場合だけProcessorから保存する。"""
        with self._queue_error_lock:
            if not self._queue_errors_pending_metadata:
                return
            errors = self._queue_errors_pending_metadata
            self._queue_errors_pending_metadata = []

        if not self._data_recorder.is_recording():
            return
        with self._metadata_lock:
            self._metadata.acquisition_errors.extend(deepcopy(error.details) for error in errors)
        self._save_metadata()

    def _stop_queue_monitor(self) -> None:
        """件数監視スレッドへ終了を通知し、ロック外で終了を待つ。"""
        self._queue_monitor_shutdown.set()
        self._queue_monitor_wakeup.set()
        thread = self._queue_monitor_thread
        if thread is not None:
            thread.join(timeout=self.STOP_TIMEOUT)
            if thread.is_alive():
                raise TimeoutError("Timed out waiting for acquisition queue monitoring to stop.")
            self._queue_monitor_thread = None

    def _put_processor_command(self, command: object, *, timeout: float | None = None) -> None:
        """命令を期限付きで投入し、満杯なら停止要求を通知して呼び出し元へ失敗を返す。

        Args:
            command (object): 記録開始・終了命令、またはProcessorの終了シグナル。
            timeout (float | None): 呼び出し元の残り待機時間。通常の投入上限を超えては待たない。
        """
        try:
            wait = self.COMMAND_PUT_TIMEOUT if timeout is None else min(self.COMMAND_PUT_TIMEOUT, timeout)
            self.data_queue.put(command, timeout=max(0.0, wait))
        except queue.Full as exc:
            self._request_queue_stop()
            raise RuntimeError("Acquisition queue is full; command was not accepted.") from exc

    # ========================== recording ==========================
    # サンプリング中のデータを保存する
    # 一つのフォルダ内にメタデータとデバイスごとのデータcsvを保存する。
    #
    # ここでは、保存開始・停止の命令をqueue監視スレッドへ送るだけ
    # 保存処理はDataRecorder()クラスに委譲する。

    def start_recording(
        self, directory: str, name: str | None = None, *,
        video_options: dict[str, VideoRecordingOptions] | None = None,
    ) -> None:
        """取得中の対象を固定し、指定された保存形式のコピーで記録を開始する。

        Args:
            directory (str): 保存先の親フォルダ。
            name (str | None): 記録フォルダ名。
            video_options (dict[str, VideoRecordingOptions] | None): カメラIDごとの保存形式。
        """
        self._ensure_not_shutting_down()
        # 停止APIが記録ロックを保持している間も、終了処理中の開始要求はすぐに拒否する。
        if self._data_recorder.is_stopping():
            raise RuntimeError("Recording is still stopping.")
        with self._recording_lock:
            # 次の開始でDataRecorderが初期化される前に、前回の確定済み終了日時を保持する。
            self._ensure_not_shutting_down()
            with self._metadata_lock:
                stopped_at = self._data_recorder.get_recording_stopped_at(self._metadata.path)
                if stopped_at is not None:
                    self._metadata.recording_stopped_at = stopped_at
            with self._state_lock:
                # 開始命令を送る時点でサンプリング中のデバイスIDを取り出す。
                device_ids = tuple(
                    device_id for device_id, device in self.device_manager.get_all().items()
                    if device.is_sampling()
                )
                if not device_ids:
                    raise RuntimeError("No device is sampling. Cannot start recording.")
                if self._data_recorder.is_recording():
                    raise RuntimeError("Recording has already started.")
                if self._data_recorder.is_stopping():
                    raise RuntimeError("Recording is still stopping.")
                if not self._is_processor_running():
                    raise RuntimeError("Processor is not running.")

                # queueを通じてqueue監視スレッドへ記録開始と設定の命令を送る
                # 記録するデバイスIDと、カメラの保存形式をコピーして渡す
                command = RecordingCommand(
                    action="start", 
                    directory=directory, 
                    name=name, 
                    device_ids=device_ids,
                    video_options=deepcopy(video_options or {}),
                )
                self._put_processor_command(command)

            # 記録が開始するまで待機する
            self._wait_recording_command(command)

    def stop_recording(self, *, timeout: float | None = None) -> None:
        """Processorへ停止要求を渡し、API側で保存の終了まで待つ。

        Args:
            timeout (float | None): ロック・命令処理・保存完了の待機上限秒。Noneなら完了まで待つ。
        """
        deadline = None if timeout is None else time.monotonic() + timeout

        # ロックを取得するまで待機する。
        acquired = self._recording_lock.acquire() if timeout is None else self._recording_lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError("Timed out waiting for the recording operation to finish.")
        try:
            if not self._is_processor_running():
                raise RuntimeError("Processor is not running.")
            
            # Processorが停止命令を取り出し、DataRecorder.stop()を呼び終えるまで待つ。
            command = RecordingCommand(action="stop")
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            self._put_processor_command(command, timeout=remaining)
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            self._wait_recording_command(command, timeout=remaining)

            # DataRecorderが動画・CSVを閉じ、最終JSONを保存するまで待つ。
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            self._data_recorder.wait_stopped(timeout=remaining)
        finally:
            self._recording_lock.release()

    def _wait_recording_command(self, command: RecordingCommand, *, timeout: float | None = None) -> None:
        """Processorが記録の開始・停止命令を処理し終えるまで待つ。

        command.doneはProcessorによる命令処理の完了を表し、保存処理の終了とは区別する。
        命令処理に失敗した場合は、実行中の終了処理の結果も待ってから元のエラーを送出する。

        Args:
            command (RecordingCommand): 共通Queueへ投入した開始・停止命令。
            timeout (float | None): 命令処理と失敗時の終了待機の上限秒。
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # 0.5秒待つ。timeoutが指定されている場合は、残り時間と0.5秒の短い方を待つ。
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if command.done.wait(timeout=0.5 if remaining is None else min(0.5, remaining)):
                break
            if not self._is_processor_running():
                raise RuntimeError("Processor stopped before completing recording command.")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the recording command to complete.")
        if command.error is not None:
            # 開始途中で失敗しても、起動済みの動画・CSVの終了処理が残る場合がある。
            # 終了処理中なら、その結果が出るまでAPI側で待つ。
            if self._data_recorder.has_pending_cleanup():
                try:
                    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                    self._data_recorder.wait_stopped(timeout=remaining)
                except Exception as cleanup_error:
                    # 同じエラーの二重出力を避け、終了処理で別のエラーが出た場合だけ記録する。
                    if cleanup_error is not command.error:
                        self.logger.error("Recording cleanup also failed: %s", cleanup_error)

            # 終了処理のエラーで原因を置き換えず、命令処理で発生したエラーを返す。
            raise command.error

    def _request_recording_stop(self, error: Exception | None = None) -> None:
        """今回の測定情報を渡して保存の終了を要求する。完了は待たない。"""
        metadata = self._metadata_dict()
        status = self._data_recorder.get_status()
        # 開始途中の失敗では、前回の測定情報を新しい記録フォルダへ渡さない。
        if metadata["path"] != status.directory:
            metadata = None
        self._data_recorder.stop(metadata, error=error)

    def _handle_recording_error(self, error: Exception) -> None:
        """Processorで検出した保存失敗を記録し、記録終了を要求する。

        Args:
            error (Exception): メタデータ保存で検出した例外。
        """
        self.logger.error("Could not save recording metadata: %s", error, exc_info=error)
        try:
            self._request_recording_stop(error=error)
        except Exception:
            # 終了要求の失敗があっても、取り出し済みの記録命令には完了・失敗を返す。
            self.logger.exception("Could not request recording finalization")

    def is_recording(self) -> bool:
        """記録中かどうかを返す。"""
        with self._state_lock:
            return self._data_recorder.is_recording()

    def get_recording_status(self):
        """記録状態を返す。"""
        status = self._data_recorder.get_status()
        with self._queue_error_lock:
            error = self._last_queue_error
            message = None
            if error is not None:
                message = str(error.details["message"])
                if error.stop_error is not None:
                    message += f" Stop failed: {error.stop_error}"
        if message:
            status.error = f"{message} {status.error}" if status.error else message
        return status

    # ========================== Processor ==========================
    # 共通データ処理スレッド
    # クラス初期化時からシャットダウンまで、デバイスが存在していなくても常に起動している
    #
    # 監視デバイスの追加 : MeasurementManager.start_sampling()
    #   websocket通信や解析処理などが実行される
    # ↓
    # データの保存 : MeasurementManager.start_recording()
    #
    # 初回の監視デバイス追加時に、共通のサンプリング開始時刻を設定する
    #   self._start_counter_ns      # サンプリング開始時刻のカウンタ [ns]
    #   self._start_datetime        # サンプリング開始時刻の実日時
    #
    # デバイスは timestamp_ns のみを返し、サンプリング開始時のカウンタ時刻を基準にして、経過時間や実時間を計算できる

    def _start_processor(self) -> None:
        """共通データ処理スレッドを起動する。すでに起動している場合は何もしない。"""
        if self._is_processor_running():
            return

        self._processor_thread = threading.Thread(
            target=self._processor_loop,
            name="measurement-processor",
        )
        self._processor_thread.start()

    def _stop_processor(self) -> None:
        """共通データ処理スレッドを終了する。"""
        with self._state_lock:
            thread = self._processor_thread
            if thread is None or not thread.is_alive():
                self._processor_thread = None
                return

            self._put_processor_command(self._stop_signal)

        # Processorの終了処理でも_state_lockを取得するため、ロック外でjoinする
        thread.join(timeout=self.STOP_TIMEOUT)

        if thread.is_alive():
            raise TimeoutError("Timed out waiting for data processing to stop.")

    def _is_processor_running(self) -> bool:
        """共通データ処理スレッドが起動中かどうかを返す。"""
        with self._state_lock:
            return self._processor_thread is not None and self._processor_thread.is_alive()

    def _processor_loop(self) -> None:
        """Queueを監視し、取得された測定データを処理する。"""
        try:
            while True:
                try:
                    data = self.data_queue.get(timeout=self.QUEUE_MONITOR_INTERVAL)
                except queue.Empty:
                    try:
                        self._save_queue_error_metadata()
                    except Exception as exc:
                        self._handle_recording_error(exc)
                    continue

                try:
                    # queueの件数に関わらずエラーメッセージが保存できるように、Processorスレッドで毎回確認する。
                    try:
                        self._save_queue_error_metadata()
                    except Exception as exc:
                        self._handle_recording_error(exc)

                    # Processor終了のシグナルを受け取った場合はループを抜ける
                    if data is self._stop_signal:
                        break

                    # recordingの保存開始・終了命令の処理。
                    if isinstance(data, RecordingCommand):
                        try:
                            if data.action == "start":
                                self._ensure_not_shutting_down()
                                with self._queue_error_lock:
                                    if self._queue_stop_requested:
                                        raise RuntimeError("Acquisition queue overflow must be resolved before recording starts.")
                                    self._last_queue_error = None
                                if data.video_options.keys() - set(data.device_ids):
                                    raise ValueError("Video options contain a device outside the recording targets.")
                                targets = {}
                                # recording開始時点でサンプリング中のデバイスIDごとに、
                                # タイプ・最新の設定コピー・保存形式をRecordingTargetにまとめる。
                                for device_id in data.device_ids:
                                    notification = self.latest_settings.get(device_id)
                                    if notification is None:
                                        raise RuntimeError(f"No settings received for device {device_id}.")
                                    
                                    targets[device_id] = RecordingTarget(
                                        type=self.device_manager.get(device_id).get_info().type,
                                        settings=deepcopy(notification.data),
                                        video_options=data.video_options.get(device_id),
                                    )
                                self._data_recorder.start(
                                    data.directory,
                                    data.name,
                                    targets=targets,
                                )
                                try:
                                    # メタデータの初期状態を作成し、最初のサンプル保存前にJSONを作成する。
                                    self._initialize_metadata(data.device_ids, data.video_options)
                                    self._save_metadata()
                                except Exception as exc:
                                    self._request_recording_stop(error=exc)
                                    raise

                            elif data.action == "stop":
                                self._request_recording_stop()
                            else:
                                raise ValueError(f"Unknown recording action: {data.action}")
                            
                        except Exception as exc:
                            data.error = exc

                        finally:
                            data.done.set()

                        continue

                    # デバイスからの取得データ・設定通知を確認
                    if not isinstance(data, DeviceData):
                        raise TypeError("Expected DeviceData from device.")
                    if data.type not in {"sample", "settings", "frame"}:
                        raise ValueError(f"Unknown device data type: {data.type}")

                    if data.type == "frame" and not isinstance(data.data, CameraFrame):
                        raise TypeError("Frame data must contain CameraFrame.")
                    if data.type != "frame" and not isinstance(data.data, dict):
                        raise TypeError("Sample and settings data must contain a dictionary.")

                    # ====================================================

                    # データを整形
                    device_id    = data.id
                    timestamp_ns = data.timestamp_ns
                    elapsed_s    = self._get_elapsed(timestamp_ns)
                    datetime     = self._get_datetime(timestamp_ns)

                    # ====== デバイスの設定変更。 ======
                    # device_idごとに最新の設定を保持。recording中はメタデータへの追記とJSONの更新を行う。
                    if data.type == "settings":
                        self.latest_settings[device_id] = deepcopy(data)
                        if self._data_recorder.is_recording_device(device_id):
                            try:
                                self._add_device_metadata(device_id, timestamp_ns, settings=data.data)
                                self._save_metadata()
                            except Exception as exc:
                                self._request_recording_stop(error=exc)
                                raise
                        self.logger.debug(
                            "Received settings: id=%s timestamp=%s elapsed=%s datetime=%s settings=%s",
                            device_id, timestamp_ns, elapsed_s, datetime, data.data,
                        )
                        continue

                    # ====== Websocket ======
                    if data.type == "frame":
                        if self.camera_preview_service is not None:
                            try:
                                notification = self.latest_settings.get(device_id)
                                if notification is None:
                                    raise RuntimeError(f"No camera settings received for {device_id}.")
                                self.camera_preview_service.offer_frame(
                                    device_id, data.data, timestamp_ns, CameraSetting.model_validate(notification.data),
                                )
                            except Exception:
                                # プレビュー用コピーが失敗しても、保存側への受け渡しは続ける。
                                self.logger.exception("Could not submit camera preview for %s", device_id)
                    
                    elif self.event_broadcaster.is_running():
                        try:
                            event = MeasurementEvent(
                                id=device_id,
                                timestamp_ns=timestamp_ns,
                                elapsed_s=elapsed_s,
                                datetime=datetime,
                                data=data.data,
                            )
                            # eventモデルをJSON対応の辞書へ変換し、配信処理へ渡す。
                            self.event_broadcaster.publish(event.model_dump(mode="json"), key=None)
                        except Exception:
                            self.logger.exception("Could not publish measurement event for %s", device_id)

                    # ====== 記録中。DataRecorderにデータを渡す ======
                    if self._data_recorder.is_recording():
                        try:
                            self._data_recorder.write(data, elapsed_s=elapsed_s)
                        except Exception as exc:
                            # 保存に失敗した場合も、終了日時をメタデータへ保存する。
                            self._request_recording_stop(error=exc)
                            raise

                    # ============================================
                    # 解析・WebSocket送信などを追加予定
                    # self._process_data(data)
                    # ============================================

                    self.logger.debug(
                        "Processed data: id=%s\n timestamp=%s\n elapsed=%s\n datetime=%s\n data=%s\n",
                        device_id,
                        timestamp_ns,
                        elapsed_s,
                        datetime,
                        data,
                    )

                except Exception:
                    self.logger.exception("Could not process measurement sample")

                finally:
                    self.data_queue.task_done()

        finally:
            try:
                # Processorの終了時にも、保存処理を閉じて最終メタデータを保存する。
                self._request_recording_stop()
            finally:
                with self._state_lock:
                    self._processor_thread = None

    # ========================== Shutdown ==========================

    def _ensure_not_shutting_down(self) -> None:
        """shutdown開始後の取得開始・記録開始・設定更新を拒否する。"""
        if self._shutdown_requested.is_set():
            raise RuntimeError("MeasurementManager is shutting down.")

    def _raise_shutdown_errors(self, errors: list[tuple[str, Exception]]) -> None:
        """段階ごとの終了失敗を、元の例外を残して呼び出し元へ返す。

        Args:
            errors (list[tuple[str, Exception]]): 失敗した段階名と例外。
        """
        if not errors:
            return
        for stage, error in errors:
            error.add_note(f"Shutdown stage: {stage}")
        if len(errors) == 1:
            raise errors[0][1]
        message = "; ".join(f"{stage}: {error}" for stage, error in errors)
        raise ExceptionGroup(f"Measurement shutdown failed: {message}", [error for _, error in errors])

    def shutdown(self) -> None:
        """依存する取得・保存の終了を確認し、未完了なら再試行用の処理を保持する。"""
        self._shutdown_requested.set()
        if not self._shutdown_lock.acquire(timeout=self.SHUTDOWN_WAIT_TIMEOUT):
            raise TimeoutError("Another measurement shutdown is still running.")
        try:
            errors = []
            for stage, stop in (("queue monitor", self._stop_queue_monitor), ("sampling", self.stop_all_sampling)):
                try:
                    stop()
                except Exception as exc:
                    errors.append((stage, exc))

            monitor = self._queue_monitor_thread
            monitor_running = monitor is not None and monitor.is_alive()
            sampling = self._is_any_device_sampling()
            if monitor_running or sampling:
                if not errors:
                    errors.append(("acquisition", TimeoutError("Acquisition or queue monitoring has not stopped.")))
                self._raise_shutdown_errors(errors)

            try:
                if self._is_processor_running():
                    # Queue末尾の終了命令が、取得停止前のデータを処理済みにする区切りとなる。
                    self.stop_recording(timeout=self.SHUTDOWN_WAIT_TIMEOUT)
                else:
                    if not self._data_recorder.is_finalizing():
                        self._request_recording_stop()
                    self._data_recorder.wait_stopped(timeout=self.SHUTDOWN_WAIT_TIMEOUT)
            except Exception as exc:
                errors.append(("recording", exc))

            if self._data_recorder.has_pending_cleanup():
                if not errors:
                    errors.append(("recording", TimeoutError("Recording cleanup has not finished.")))
                self._raise_shutdown_errors(errors)

            if not self._is_processor_running() and self.data_queue.qsize():
                errors.append(("data queue", RuntimeError(
                    f"Processor stopped with {self.data_queue.qsize()} unprocessed queue items."
                )))

            try:
                self._stop_processor()
            except Exception as exc:
                errors.append(("processor", exc))

            if not self._is_processor_running() and self.data_queue.empty():
                self._start_counter_ns = None
                self._start_datetime = None
            self._raise_shutdown_errors(errors)
            self.logger.info("MeasurementManager has been shutdown.")
        finally:
            self._shutdown_lock.release()

    # ========================== Time ==========================

    def _get_elapsed(self, counter_ns: int) -> float:
        """
        測定開始からの経過時間を取得する。

        Args:
            counter_ns (int): 対象データのカウンタ時刻 [ns]。

        Returns:
            return (float): 測定開始からの経過時間 [s]。
        """
        if self._start_counter_ns is None:
            raise RuntimeError("Measurement has not started yet.")

        return counter_to_elapsed_seconds(counter_ns, self._start_counter_ns)

    def _get_datetime(self, counter_ns: int):
        """
        カウンタ時刻を測定開始基準の実日時に変換する。

        Args:
            counter_ns (int): 対象データのカウンタ時刻 [ns]。

        Returns:
            return (datetime): 対象データに対応する実日時。
        """
        if self._start_counter_ns is None or self._start_datetime is None:
            raise RuntimeError("Measurement has not started yet.")

        return counter_to_datetime(
            counter_ns,
            self._start_counter_ns,
            self._start_datetime,
        )

