"""複数機器の取得データを、保存と画面への配信へ受け渡す。

機器を直接操作するドライバーは、各スレッドで取得した数値・画像・設定通知を
共通Queueへ入れる。Processorスレッドが順に取り出し、設定履歴を更新する。
Queueへ渡す各データには測定期間を添え、配信するIDと時刻計算に同じ期間を使う。
数値の配信、表示用画像の生成、データの保存は、それぞれの担当サービスへ依頼する。
動画・CSVの終了と、設定や日時をまとめたmetadata.jsonの保存はDataRecorderが担当する。

別のスレッドがQueueの件数と投入失敗を監視し、異常時は全機器の取得停止を試みる。
Processorは受付済みのデータを処理し続ける。取得の停止と記録全体の終了は別々に扱う。
"""

import queue
import threading
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel

from backend.app.models.events import MeasurementEvent
from backend.app.models.devices import CameraSetting
from backend.app.models.measurement import CameraFrame, DeviceData, MeasurementPeriod, MeasurementStatus
from backend.app.models.recordings import RecordingStatus, RecordingTarget, VideoRecordingOptions
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
    """記録対象カメラのFPS・ROI変更を、機器への適用前に拒否したことを表す。"""


@dataclass
class QueueError:
    """Queue異常の検出情報と、取得停止の失敗理由を保持する。

    状態取得用とメタデータ保存待ちの一覧で同じオブジェクトを参照する。
    detailsには理由・機器ID・件数・検出日時・説明を保持する。
    取得停止にも失敗した場合はstop_errorへ理由を追記する。
    """
    details: dict[str, object]
    stop_error: str | None = None


@dataclass
class RecordingCommand:
    """記録命令とその処理結果を、呼び出し元とProcessorの間で受け渡す。

    測定データと同じQueueへ入れ、先行データの処理後に実行する。
    Processorは成否をerrorへ反映してdoneを通知する。停止命令の完了後も、
    動画・CSV・最終JSONの終了処理は別スレッドで続く場合がある。
    """
    action: str                         # startで保存開始、stopで保存の新規受付を終了する。
    directory: str | None = None        # 記録フォルダを作る親ディレクトリ。
    name: str | None = None
    device_ids: tuple[str, ...] = ()    # 開始要求時に取得中だった機器ID。この記録の対象を固定する。
    measurement_id: str | None = None   # 開始要求時の測定期間。命令の処理時には取り直さない。
    video_options: dict[str, VideoRecordingOptions] = field(default_factory=dict)
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None


@dataclass
class Metadata:
    """一回の記録の保存先・時刻・機器の設定履歴をまとめ、metadata.jsonへ渡す。

    measurementの時刻は共通の経過時間の基準、recordingの時刻は記録期間を表す。
    devicesには機器IDごとにtimestamp_ns・info・settingsの履歴を古い順に保持する。
    動画の終了結果と確定した記録終了日時は、DataRecorderが最終JSONへ追加する。
    """
    name: str | None = None
    path: str | None = None
    measurement_started_at: datetime | None = None   # 機器間で共通の経過時間を計算するための取得開始日時。
    measurement_start_counter_ns: int | None = None  # 同じ取得開始時点のPCカウンタ値。単位はns。
    recording_started_at: datetime | None = None
    recording_stopped_at: datetime | None = None     # 保存リソースの終了後、DataRecorderで確定した日時。
    devices: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    video_options: dict[str, dict[str, str]] = field(default_factory=dict)
    acquisition_errors: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class _SamplingData:
    """取得データと、その取得開始の成否・測定期間を持つ投入元。"""

    data: DeviceData
    source: "_SamplingQueue"


class _SamplingQueue:
    """ドライバーの投入先として、測定期間を添えたデータを共通Queueへ中継する。

    取得開始ごとに作成し、periodは現在の測定期間が切り替わっても置き換えない。
    MeasurementManagerは開始成功時にacceptedをTrueにし、成否が確定したらreadyを通知する。
    Processorはreadyを待ち、acceptedがTrueのデータだけを処理する。
    満杯時は機器IDを管理側へ通知する。機器の停止や終了待機はここでは行わない。
    """

    def __init__(
        self, output: queue.Queue, period: MeasurementPeriod, *, on_queue_full: Callable[[str], None],
    ):
        self.output = output
        self.period = period
        self.on_queue_full = on_queue_full
        self.ready = threading.Event()
        self.accepted = False

    def put(self, data: DeviceData, block: bool = True, timeout: float | None = None) -> None:
        """測定期間を添えて投入する。満杯時は機器IDを管理側へ通知し、queue.Fullを再送出する。"""
        if self.ready.is_set() and not self.accepted:
            raise RuntimeError("Sampling did not start successfully.")
        try:
            self.output.put(_SamplingData(data, self), block=block, timeout=timeout)
        except queue.Full as exc:
            self.on_queue_full(data.id)  # 停止要求だけを通知し、取得スレッドの終了は待たない。
            message = (
                "Settings were applied, but the acquisition queue is full; settings notification was not queued."
                if data.type == "settings" else "Acquisition queue is full; sample was not queued."
            )
            raise queue.Full(message) from exc


class MeasurementManager:
    """機器の取得と記録を操作し、共通Queueの処理を調整する。

    start_samplingで機器の取得を、start_recordingで取得データの保存を開始する。
    記録開始時に取得中の全機器を保存対象とし、後から取得を始めた機器は追加しない。
    記録対象カメラのFPS（毎秒の撮影枚数）とROI（画像の切り出し範囲）は開始時の値に固定する。

    生成時にProcessorとQueue件数監視のスレッドを起動する。機器の取得と記録は個別に
    停止でき、stop_recordingは動画・CSV・最終JSONの終了結果まで待つ。
    Processor内では記録終了の要求までを行い、その完了待ちは外部のスレッドで行う。

    利用終了時はshutdownを呼ぶ。終了に失敗した場合は必要な状態を保持して例外を返し、
    停止を再試行できる。shutdown開始後の取得開始・記録開始・設定更新は拒否する。
    """

    # Processorと件数監視スレッドのjoinに使う待機秒数。
    STOP_TIMEOUT = 2.0
    # 件数は画像のバイト数によらず数える。監視閾値と投入上限を分ける。
    QUEUE_MAXSIZE = 350
    QUEUE_STOP_THRESHOLD = 300
    QUEUE_MONITOR_INTERVAL = 0.1
    COMMAND_PUT_TIMEOUT = 1.0
    SHUTDOWN_WAIT_TIMEOUT = 10.0

    def __init__(
        self, device_manager, event_broadcaster, camera_preview_service: CameraPreviewService | None = None,
        *, server_instance_id: str | None = None,
    ) -> None:
        """依存サービスを保持し、ProcessorとQueue件数監視を起動する。

        Args:
            device_manager: 登録機器の列挙と、IDによるドライバー取得を行うサービス。
            event_broadcaster: 数値イベントを購読先へ配信するサービス。
            camera_preview_service (CameraPreviewService | None): 画像のコピー・JPEG生成・配信を
                行うサービス。Noneならプレビューを生成しない。
            server_instance_id (str | None): サービス起動ごとのID。単独で使う場合は未指定で生成する。
        """
        self.device_manager    = device_manager
        self.event_broadcaster = event_broadcaster
        self.camera_preview_service = camera_preview_service
        self.server_instance_id = server_instance_id if server_instance_id is not None else str(uuid4())
        # 直近の取得開始に成功した測定期間。停止や次の開始失敗でも保持する。
        self._measurement_period: MeasurementPeriod | None = None

        self.data_queue = queue.Queue(maxsize=self.QUEUE_MAXSIZE)
        self.latest_settings: dict[str, DeviceData] = {}    # 機器IDごとに、Processorが最後に処理した設定通知を保持する。
        self._processor_thread = None
        self._state_lock = threading.RLock()                # 取得の開始・停止とProcessorの参照を保護する。再入を許す。
        self._recording_lock = threading.Lock()             # 記録操作と設定適用の前後関係を固定する。
        self._metadata_lock = threading.Lock()              # メタデータのコピー・初期化・履歴追加を保護する。
        self._shutdown_requested = threading.Event()
        self._shutdown_lock = threading.Lock()
        # 取得停止は_state_lockを持ってドライバーの終了を待つため、
        # 取得スレッドから呼ぶQueue異常通知には別のロックを使う。
        self._queue_error_lock = threading.Lock()
        self._last_queue_error: QueueError | None = None
        # 最新エラーと保存待ち一覧で、同じQueueErrorを参照する。
        # 監視スレッドが追記した取得停止失敗の説明も、状態取得側から読める。
        self._queue_errors_pending_metadata: list[QueueError] = []
        self._queue_stop_requested = False
        self._queue_stop_attempted = False  # 全取得の停止を試み終えたか。成功したことを表す値ではない。
        # 取得側の通知による起床と、監視スレッド自体の終了要求を分ける。
        self._queue_monitor_wakeup = threading.Event()
        self._queue_monitor_shutdown = threading.Event()
        self._queue_monitor_thread: threading.Thread | None = None

        self._stop_signal = object()

        self._data_recorder = DataRecorder(server_instance_id=self.server_instance_id)
        self._metadata = Metadata()

        # 取得データのカウンタ差から経過秒と実日時を求めるため、二つの基準を持つ。
        self._start_counter_ns: int | None = None
        self._start_datetime = None

        self.logger = setup_logger(__class__.__name__)

        self._start_processor()
        self._queue_monitor_thread = threading.Thread(
            target=self._queue_monitor_loop, name="measurement-queue-monitor",
        )
        self._queue_monitor_thread.start()

    def get_settings(self, device_id: str) -> BaseModel:
        """指定機器の適用済み設定を返す。

        Args:
            device_id (str): 設定を取得する機器のID。

        Returns:
            BaseModel: ドライバーが返す機器固有の設定。
        """
        device = self.device_manager.get(device_id)
        return device.get_settings()

    def set_settings(self, device_id: str, settings: dict[str, Any] | BaseModel) -> BaseModel:
        """記録中の変更制限を確認し、機器へ設定一式を適用する。

        記録対象カメラのFPSとROIは、記録開始時の値と比較して変更を拒否する。
        同じ値の再送と、それ以外の項目の変更はドライバーへ渡す。記録開始・停止との
        前後関係が変わらないよう、記録操作と同じロックを保持して検証と適用を行う。

        Args:
            device_id (str): 設定を適用する機器のID。
            settings (dict[str, Any] | BaseModel): 機器が必要とする設定項目の一式。

        Returns:
            BaseModel: ドライバーが返した適用済みの設定。

        Raises:
            RecordingSettingsConflictError: 記録対象カメラのFPSまたはROIを変える場合。
            RuntimeError: shutdownが始まっている場合。
            Exception: 設定モデルの検証または機器への適用に失敗した場合。
        """
        # 記録開始・終了と同じロックを使い、検証後に記録対象が切り替わることを防ぐ。
        with self._recording_lock:
            self._ensure_not_shutting_down()
            device = self.device_manager.get(device_id)

            # 履歴の先頭にある記録開始時の設定と比較する。
            if self._get_locked_recording_settings(
                device.get_info().type, self._data_recorder.is_recording_device(device_id),
            ):
                values = settings.model_dump() if isinstance(settings, BaseModel) else settings
                requested = CameraSetting.model_validate(values)
                with self._metadata_lock:
                    recorded = CameraSetting.model_validate(self._metadata.devices[device_id][0]["settings"])
                if requested.fps != recorded.fps or requested.roi != recorded.roi:
                    raise RecordingSettingsConflictError("Cannot change FPS or ROI while recording this camera.")

            return device.set_settings(settings)

    def get_metadata(self) -> Metadata:
        """保持中のメタデータのコピーに、確定済みの記録終了日時を反映して返す。

        Returns:
            Metadata: 呼び出し元で変更しても内部状態に影響しないコピー。
        """
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
        """機器の情報と設定を、指定時刻の履歴として追記する。

        Args:
            device_id (str): 履歴を追加する機器のID。
            timestamp_ns (int): 設定通知などに付けられたPCのカウンタ時刻。単位はns。
            info (dict[str, object] | None): 保存する機器情報。Noneならドライバーから取得する。
            settings (dict[str, object] | None): 保存する設定。Noneならドライバーから取得する。

        Raises:
            RuntimeError: 情報や設定の取得、変換、履歴追加に失敗した場合。機器IDを説明に含める。
        """
        try:
            if info is None or settings is None:
                device = self.device_manager.get(device_id)
                if info is None:
                    info = device.get_info().model_dump(mode="json")
                if settings is None:
                    settings = device.get_settings().model_dump(mode="json")

            # 後から呼び出し元や機器が辞書を変更しても、保存済みの履歴へ影響させない。
            entry = {
                "timestamp_ns": timestamp_ns,
                "info": deepcopy(info),
                "settings": deepcopy(settings),
            }
            with self._metadata_lock:
                self._metadata.devices.setdefault(device_id, []).append(entry)
        except Exception as exc:
            raise RuntimeError(f"Could not collect metadata for device {device_id}: {exc}") from exc

    def _initialize_metadata(self,
                             device_ids: tuple[str, ...],
                             video_options: dict[str, VideoRecordingOptions]
                             ) -> None:
        """開始済みの保存先に対応するメタデータと、対象機器の初期履歴を作る。

        ProcessorがDataRecorderの記録開始に成功した後、最初のデータを保存する前に呼ぶ。
        以前の記録の履歴を置き換え、開始命令より前に受信した最新の設定通知を登録する。
        この関数ではJSONへ保存しない。

        Args:
            device_ids (tuple[str, ...]): 開始要求時に取得中だった記録対象の機器ID。
            video_options (dict[str, VideoRecordingOptions]): カメラIDごとの動画保存形式。

        Raises:
            RuntimeError: 対象機器の設定通知がない場合、または初期履歴の収集に失敗した場合。
        """
        status = self._data_recorder.get_status()
        with self._metadata_lock:
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

        # 開始命令より前にProcessorが受信した設定通知を、最初の履歴に使う。
        # ドライバーへ再問い合わせして、後から適用された設定に置き換わることを避ける。
        for device_id in device_ids:
            notification = self.latest_settings.get(device_id)
            if notification is None:
                raise RuntimeError(f"No settings received for device {device_id}.")
            self._add_device_metadata(
                device_id, notification.timestamp_ns, settings=notification.data,
            )

    def _metadata_dict(self) -> dict:
        """メタデータをコピーし、datetimeをISO形式の文字列にした辞書を返す。"""
        with self._metadata_lock:
            metadata = asdict(self._metadata)

        for key, value in metadata.items():
            if isinstance(value, datetime):
                metadata[key] = value.isoformat()
        return metadata

    def _save_metadata(self) -> None:
        """現在のメタデータを、作成済みの記録フォルダへ保存する。

        書き込みの例外は呼び出し元へ渡す。
        """
        self._data_recorder.write_metadata(self._metadata_dict())

    def start_sampling(self, device_id: str) -> None:
        """指定機器へ共通Queueを渡し、データ取得を開始する。

        カメラではプレビューの受付も開始する。取得中の機器がない場合は、残っているQueueの
        処理を待つ。記録中または終了処理中なら時間基準を保持し、それ以外は新しい時間基準を
        作る。取得だけではファイル保存を開始しない。

        Args:
            device_id (str): 取得を開始する登録済み機器のID。取得中なら何もしない。

        Raises:
            RuntimeError: shutdown中、またはQueue異常による全取得の停止をまだ確認できない場合。
            Exception: ドライバーやプレビューの開始に失敗した場合。
        """
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
            self._start_processor()     # 終了済みのProcessorがあれば、新しい処理スレッドを用意する。

            new_period = None
            if not self._is_any_device_sampling():
                # 前の取得データを、新しい時間基準で処理しないよう完了を待つ。
                self.data_queue.join()
                with self._queue_error_lock:
                    self._queue_stop_requested = False
                    self._queue_stop_attempted = False
                    if not self._data_recorder.is_recording() and not self._data_recorder.is_stopping():
                        self._last_queue_error = None
                if not self._data_recorder.is_recording() and not self._data_recorder.is_stopping():
                    self._start_counter_ns = get_counter_ns()
                    self._start_datetime = get_datetime()
                    new_period = MeasurementPeriod(
                        measurement_id=str(uuid4()),
                        started_at=self._start_datetime,
                        start_counter_ns=self._start_counter_ns,
                    )

            output = _SamplingQueue(
                self.data_queue, new_period or self._measurement_period, on_queue_full=self._request_queue_stop,
            )
            try:
                # 画像が届いたときに表示側が受け付けられるよう、カメラのプレビューを先に準備する。
                if self.camera_preview_service is not None and device.get_info().type == "camera":
                    self.camera_preview_service.start_camera(device_id)

                device.start_sampling(output)
                if new_period is not None:
                    self._measurement_period = new_period
                output.accepted = True

            except Exception:
                if self.camera_preview_service is not None:
                    self.camera_preview_service.stop_camera(device_id)

                if not self._is_any_device_sampling() and not self._data_recorder.is_recording() and not self._data_recorder.is_stopping():
                    self._start_counter_ns = None
                    self._start_datetime = None

                self.logger.exception("Could not start sampling for device %s", device_id)
                raise
            finally:
                # 開始失敗時も待機を解除する。Processorは_state_lockを取らずに成否を確認する。
                output.ready.set()

    def get_measurement_status(self) -> MeasurementStatus:
        """直近の成功した測定期間と、取得中の機器IDをまとめて返す。"""
        with self._state_lock:
            period = self._measurement_period
            return MeasurementStatus(
                server_instance_id=self.server_instance_id,
                measurement_id=period.measurement_id if period is not None else None,
                started_at=period.started_at if period is not None else None,
                sampling_device_ids=[
                    device_id for device_id, device in self.device_manager.get_all().items()
                    if device.is_sampling()
                ],
                error=self._queue_error_message(),
            )

    def stop_sampling(self, device_id: str) -> None:
        """指定機器のプレビュー受付を止め、取得スレッドの終了を待つ。

        取得済みのデータは共通Queueに残し、Processorが処理を続ける。機器の接続状態と
        記録全体は維持する。すでに取得停止済みでもプレビューの解除は行う。

        Args:
            device_id (str): 取得を停止する機器のID。

        Raises:
            Exception: 機器が見つからない場合、または取得・プレビューの停止に失敗した場合。
        """
        with self._state_lock:
            device = self.device_manager.get(device_id)
            if self.camera_preview_service is not None:
                self.camera_preview_service.stop_camera(device_id)  # 取得状態に関係なく、この機器の表示用画像の受付を解除する。
            if not device.is_sampling():
                return

            device.stop_sampling()

    def stop_all_sampling(self) -> None:
        """登録された全機器の取得とプレビューの停止を順に試みる。

        一台の停止失敗で中断せず、残りの機器も処理する。記録全体の終了とProcessorの
        終了は行わない。

        Raises:
            RuntimeError: 一台以上の停止に失敗した場合。各機器の詳しい例外はログへ出す。
        """
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
        """指定機器が取得中かを返す。"""
        with self._state_lock:
            device = self.device_manager.get(device_id)
            return device.is_sampling()

    def _is_any_device_sampling(self) -> bool:
        """登録機器のうち一台でも取得中ならTrueを返す。"""
        for device in self.device_manager.get_all().values():
            if device.is_sampling():
                return True

        return False

    def _request_queue_stop(
        self, device_id: str | None = None, *, reason: str = "queue_full", queue_size: int | None = None,
    ) -> None:
        """Queue異常の理由を保存し、全取得の停止を件数監視スレッドへ依頼する。

        機器の取得スレッドからも呼ばれるため、この関数では機器の停止や終了待機を行わない。
        同じ取得期間で受け付ける最初の理由を保持し、追加の通知は無視する。
        実際の取得停止と、その失敗理由の追記は件数監視スレッドが行う。

        Args:
            device_id (str | None): Queueへ投入できなかった機器のID。管理側の検出ではNone。
            reason (str): 投入失敗はqueue_full、監視閾値の超過はqueue_threshold_exceeded。
            queue_size (int | None): 検出時の件数。Noneなら呼び出された時点の件数を使う。
        """
        with self._queue_error_lock:
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

        self._queue_monitor_wakeup.set()        # 件数監視スレッドのwaitを解除する。取得停止そのものはそのスレッドで行う。

    def _queue_monitor_loop(self) -> None:
        """件数監視スレッドで異常を検出し、全取得の停止を試みる。

        同じ取得期間では一度だけ試行し、停止失敗は状態取得用のエラーとログへ残す。
        記録は終了せず、受付済みデータの処理をProcessorに続けさせる。
        """
        while not self._queue_monitor_shutdown.is_set():
            self._queue_monitor_wakeup.wait(timeout=self.QUEUE_MONITOR_INTERVAL)
            self._queue_monitor_wakeup.clear()
            if self._queue_monitor_shutdown.is_set():
                break

            count = self.data_queue.qsize()
            if count > self.QUEUE_STOP_THRESHOLD:
                self._request_queue_stop(reason="queue_threshold_exceeded", queue_size=count)

            # 未試行の停止要求だけを実行する。停止が失敗した後も自動では繰り返さない。
            with self._queue_error_lock:
                should_stop = self._queue_stop_requested and not self._queue_stop_attempted
            if not should_stop:
                continue

            try:
                # 取得スレッドだけを停止し、Processorと記録は残して受付済みデータを処理する。
                self.stop_all_sampling()
            except Exception as exc:
                with self._queue_error_lock:
                    self._last_queue_error.stop_error = str(exc)
                self.logger.exception("Could not stop sampling after acquisition queue overflow")
            finally:
                with self._queue_error_lock:
                    self._queue_stop_attempted = True

    def _save_queue_error_metadata(self) -> None:
        """保存待ちのQueueエラーを消費し、記録中ならメタデータへ追加して保存する。

        Processorから呼び、履歴更新の順序をデータ処理と揃える。
        記録外でも待ち一覧は消費するが、状態取得用の最新エラーは保持する。
        保存失敗は呼び出し元へ渡す。
        """
        with self._queue_error_lock:
            if not self._queue_errors_pending_metadata:
                return
            errors = self._queue_errors_pending_metadata
            self._queue_errors_pending_metadata = []

        # 記録外の理由はJSONへ書かず消費する。状態取得用の最新エラーは消さない。
        if not self._data_recorder.is_recording():
            return
        with self._metadata_lock:
            self._metadata.acquisition_errors.extend(deepcopy(error.details) for error in errors)
        self._save_metadata()

    def _stop_queue_monitor(self) -> None:
        """件数監視スレッドへ終了を通知し、終了確認後にスレッドの参照を外す。

        Raises:
            TimeoutError: STOP_TIMEOUT秒待ってもスレッドが残っている場合。参照は保持する。
        """
        self._queue_monitor_shutdown.set()
        self._queue_monitor_wakeup.set()
        thread = self._queue_monitor_thread
        if thread is not None:
            thread.join(timeout=self.STOP_TIMEOUT)
            if thread.is_alive():
                raise TimeoutError("Timed out waiting for acquisition queue monitoring to stop.")
            self._queue_monitor_thread = None

    def _put_processor_command(self, command: object, *, timeout: float | None = None) -> None:
        """開始・停止命令または終了シグナルを、共通Queueへ投入する。

        Args:
            command (object): 記録開始・停止のRecordingCommand、またはProcessor終了シグナル。
            timeout (float | None): 呼び出し元に残された待機秒数。Noneでも投入待機は
                COMMAND_PUT_TIMEOUT秒までとし、指定時はそれと残り時間の短い方を使う。

        Raises:
            RuntimeError: 待機しても満杯の場合。命令は投入せず、全取得の停止要求を通知する。
        """
        try:
            wait = self.COMMAND_PUT_TIMEOUT if timeout is None else min(self.COMMAND_PUT_TIMEOUT, timeout)
            self.data_queue.put(command, timeout=max(0.0, wait))
        except queue.Full as exc:
            self._request_queue_stop()
            raise RuntimeError("Acquisition queue is full; command was not accepted.") from exc

    def start_recording(
        self, directory: str, name: str | None = None, *,
        video_options: dict[str, VideoRecordingOptions] | None = None,
    ) -> None:
        """要求時点で取得中の全機器を対象に、記録の開始完了まで待つ。

        開始命令を共通Queueへ入れ、Processorが先行する設定通知を処理してから保存先と
        対象機器の保存処理を作成する。呼び出し後に取得を開始した機器は対象へ追加しない。
        Processor自身から呼ぶと自分の命令完了を待つため、外部のスレッドから呼ぶ。

        Args:
            directory (str): 記録フォルダを新規作成する親ディレクトリ。
            name (str | None): 記録フォルダの名前。Noneの場合の名前はDataRecorderが決める。
            video_options (dict[str, VideoRecordingOptions] | None): 対象カメラごとの動画保存形式。
                呼び出し元の変更が命令へ影響しないようコピーして渡す。

        Raises:
            RuntimeError: shutdown中、取得機器なし、記録中・終了処理中、Processor停止中、
                Queue異常や投入失敗などで開始を受け付けられない場合。
            Exception: 対象設定の検証、保存先作成、保存処理や初期メタデータ保存に失敗した場合。
        """
        self._ensure_not_shutting_down()
        # 別の停止操作が記録ロックを保持中でも、終了処理中の開始はロック待ち前に拒否する。
        if self._data_recorder.is_stopping():
            raise RuntimeError("Recording is still stopping.")
        with self._recording_lock:
            self._ensure_not_shutting_down()
            with self._metadata_lock:
                # 次の開始でDataRecorderの情報が初期化される前に、前回の確定した終了日時を拾う。
                stopped_at = self._data_recorder.get_recording_stopped_at(self._metadata.path)
                if stopped_at is not None:
                    self._metadata.recording_stopped_at = stopped_at
            with self._state_lock:
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

                # 先行する設定通知が処理されてから、Processorが記録を開始する。
                command = RecordingCommand(
                    action="start",
                    directory=directory,
                    name=name,
                    device_ids=device_ids,
                    measurement_id=self._measurement_period.measurement_id,
                    video_options=deepcopy(video_options or {}),
                )
                self._put_processor_command(command)

            # Processorが保存先と対象の保存処理を準備し、初期JSONを書いた結果を待つ。
            self._wait_recording_command(command)

    def stop_recording(self, *, timeout: float | None = None) -> None:
        """記録停止命令をProcessorへ渡し、保存の終了結果まで待つ。

        停止命令より前にQueueへ入ったデータをProcessorが処理してから保存受付を止める。
        命令処理の完了に続き、DataRecorderが動画・CSVを閉じて最終JSONを保存する処理を待つ。
        機器の取得は継続する。Processor自身からは呼ばず、記録終了の要求だけを行う。

        Args:
            timeout (float | None): ロック取得・命令投入・命令処理・保存終了の各待機に使う
                共通の期限までの秒数。Noneなら命令処理と保存終了の期限を設けない。
                命令投入には別途最大COMMAND_PUT_TIMEOUT秒の上限がある。

        Raises:
            TimeoutError: ロック、命令処理、保存終了の待機が期限に達した場合。
            RuntimeError: Processor停止やQueue満杯で停止命令を渡せない場合。
            Exception: 停止命令の処理や保存終了に失敗した場合。

        待機のタイムアウトは、投入済みの命令や開始済みの保存終了処理を取り消さない。
        """
        deadline = None if timeout is None else time.monotonic() + timeout

        acquired = self._recording_lock.acquire() if timeout is None else self._recording_lock.acquire(timeout=timeout)
        if not acquired:
            raise TimeoutError("Timed out waiting for the recording operation to finish.")
        try:
            if not self._is_processor_running():
                raise RuntimeError("Processor is not running.")

            # 停止命令より前のデータを処理した後、DataRecorderの保存受付を止める。
            command = RecordingCommand(action="stop")
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            self._put_processor_command(command, timeout=remaining)
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            self._wait_recording_command(command, timeout=remaining)

            # 命令完了とは別に、動画・CSV・最終JSONの終了を待つ。
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            self._data_recorder.wait_stopped(timeout=remaining)
        finally:
            self._recording_lock.release()

    def _wait_recording_command(self, command: RecordingCommand, *, timeout: float | None = None) -> None:
        """投入済みの記録命令の完了を待ち、成否を確認する。

        停止命令の成功はDataRecorderへの終了要求までを表す。保存終了は呼び出し元で待つ。
        命令失敗時は残っている終了処理の結果も確認し、後始末の失敗より元の命令エラーを優先する。

        Args:
            command (RecordingCommand): 共通Queueへ投入済みの開始・停止命令。
            timeout (float | None): 命令処理と失敗時の終了待機に使う残り秒数。Noneなら期限なし。

        Raises:
            RuntimeError: 完了前にProcessorが停止した場合。
            TimeoutError: 命令完了の待機期限に達した場合。
            Exception: command.errorに保持された例外。失敗後の終了待機がタイムアウトしても優先する。
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # Processorの停止を検出できるよう、完了待ちを最大0.5秒ごとに区切る。
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if command.done.wait(timeout=0.5 if remaining is None else min(0.5, remaining)):
                break
            if not self._is_processor_running():
                raise RuntimeError("Processor stopped before completing recording command.")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the recording command to complete.")
        if command.error is not None:
            # 開始失敗時でも、作成済みの動画やCSVの終了処理が別スレッドで進む場合がある。
            # has_pending_cleanupは、動作中の処理・未解放ファイル・未保存JSONが残る間Trueを返す。
            if self._data_recorder.has_pending_cleanup():
                try:
                    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                    self._data_recorder.wait_stopped(timeout=remaining)
                except Exception as cleanup_error:
                    # 元の命令エラーと同じ例外なら再出力せず、終了処理で生じた別の失敗だけをログに残す。
                    if cleanup_error is not command.error:
                        self.logger.error("Recording cleanup also failed: %s", cleanup_error)

            raise command.error

    def _request_recording_stop(self, error: Exception | None = None) -> None:
        """現在の記録のメタデータを添えて、DataRecorderへ終了を要求する。

        保存終了は待たず、終了要求の例外は呼び出し元へ渡す。

        Args:
            error (Exception | None): 記録終了の原因となった例外。通常停止ではNone。
        """
        metadata = self._metadata_dict()
        status = self._data_recorder.get_status()
        # DataRecorderだけが新しい保存先を作成し、メタデータが前回のままのケースを除く。
        # 保存先が一致しないメタデータは渡さず、DataRecorderが持つ情報で終了させる。
        if metadata["path"] != status.directory:
            metadata = None
        self._data_recorder.stop(metadata, error=error)

    def _handle_recording_error(self, error: Exception) -> None:
        """保存失敗をログに出して記録終了を要求し、終了要求の失敗もログに残して戻る。

        Args:
            error (Exception): Queueエラー情報のメタデータ保存で発生した例外。終了理由として渡す。
        """
        self.logger.error("Could not save recording metadata: %s", error, exc_info=error)
        try:
            self._request_recording_stop(error=error)
        except Exception:
            # この関数は、取り出し済みの記録命令を判定する前にも呼ばれる。
            # 終了要求の例外でその判定とdone通知を飛ばさないよう、ログに残して戻る。
            self.logger.exception("Could not request recording finalization")

    def is_recording(self) -> bool:
        """保存受付中ならTrueを返す。保存の終了処理中はFalse。"""
        with self._state_lock:
            return self._data_recorder.is_recording()

    def get_recording_status(self) -> RecordingStatus:
        """記録の識別情報・対象の保存状態・現在の設定制限とエラーを返す。

        Returns:
            RecordingStatus: DataRecorderの状態に、最新のQueue異常と取得停止失敗の説明を
                errorへ追加した結果。元の保存エラーがあれば、その説明も併記する。
        """
        status = self._data_recorder.get_status()
        for target in status.targets.values():
            target.locked_settings = self._get_locked_recording_settings(target.type, status.is_recording)
        message = self._queue_error_message()
        if message:
            status.error = f"{message} {status.error}" if status.error else message
        return status

    @staticmethod
    def _get_locked_recording_settings(device_type: str, is_recording_device: bool) -> list[str]:
        """設定APIが値の変更を拒否する項目を返す。同じ値の再送は許可する。"""
        return ["fps", "roi"] if device_type == "camera" and is_recording_device else []

    def _queue_error_message(self) -> str | None:
        """共通Queueの検出理由と、取得停止にも失敗した場合の説明を返す。"""
        with self._queue_error_lock:
            error = self._last_queue_error
            message = None
            if error is not None:
                message = str(error.details["message"])
                if error.stop_error is not None:
                    message += f" Stop failed: {error.stop_error}"
        return message

    def _start_processor(self) -> None:
        """Processorを起動する。動作中なら何もしない。"""
        if self._is_processor_running():
            return

        self._processor_thread = threading.Thread(
            target=self._processor_loop,
            name="measurement-processor",
        )
        self._processor_thread.start()

    def _stop_processor(self) -> None:
        """Queue末尾へ終了シグナルを入れ、Processorが終了するまで待つ。

        取得と保存の終了を確認した後、Processor以外のスレッドから呼ぶ。
        Processorは先行データを処理してから終了シグナルを取り出し、記録終了を要求して抜ける。
        この関数はDataRecorderの保存終了までは待たない。

        Raises:
            RuntimeError: Queueが満杯のままで終了シグナルを投入できない場合。
            TimeoutError: シグナル投入後にSTOP_TIMEOUT秒待ってもProcessorが残る場合。
        """
        with self._state_lock:
            thread = self._processor_thread
            if thread is None or not thread.is_alive():
                self._processor_thread = None
                return

            self._put_processor_command(self._stop_signal)

        # 終了するProcessorも最後に_state_lockを取得するため、
        # 呼び出し側はロックを解放してから、そのスレッドの終了を待つ。
        thread.join(timeout=self.STOP_TIMEOUT)

        if thread.is_alive():
            raise TimeoutError("Timed out waiting for data processing to stop.")

    def _is_processor_running(self) -> bool:
        """Processorスレッドが動作中かを返す。"""
        with self._state_lock:
            return self._processor_thread is not None and self._processor_thread.is_alive()

    def _processor_loop(self) -> None:
        """共通Queueの命令・設定通知・数値・画像を順に処理する。

        記録命令には成否を通知し、設定履歴を更新して、データを配信・プレビュー・保存へ渡す。
        表示や配信の失敗では保存を続け、保存失敗では記録終了を要求する。
        各項目の処理失敗はログに残して次へ進み、終了シグナルを受けたらループを抜ける。
        """
        try:
            while True:
                try:
                    data = self.data_queue.get(timeout=self.QUEUE_MONITOR_INTERVAL)
                except queue.Empty:
                    # データが来なくても、検出済みのQueue異常を記録中のメタデータへ保存する。
                    try:
                        self._save_queue_error_metadata()
                    except Exception as exc:
                        # 保存失敗を記録終了の理由として渡す。終了要求の失敗もログへ出し、ループは続ける。
                        self._handle_recording_error(exc)
                    continue

                try:
                    # データや命令の処理前に、保存待ちのQueue異常をメタデータへ追記する。
                    # 失敗しても下の命令処理へ進み、命令の完了通知を返せる状態を保つ。
                    try:
                        self._save_queue_error_metadata()
                    except Exception as exc:
                        self._handle_recording_error(exc)

                    if data is self._stop_signal:
                        break

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
                                # 対象IDは開始要求時の一覧を使い、設定は先行する通知から取得する。
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
                                    measurement_id=data.measurement_id,
                                )
                                try:
                                    # 保存開始後、サンプルを書き込む前に対象の設定履歴と初期JSONを作る。
                                    # ここで失敗した場合も、作成済みのCSVや動画の終了を要求する。
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

                    if not isinstance(data, _SamplingData):
                        raise TypeError("Expected sampling data with a measurement period.")
                    # 開始中にも設定やサンプルが届く。開始結果は_state_lockを取らずに待つ。
                    data.source.ready.wait()
                    if not data.source.accepted:
                        continue
                    period = data.source.period
                    data = data.data

                    if not isinstance(data, DeviceData):
                        raise TypeError("Expected DeviceData from device.")
                    if data.type not in {"sample", "settings", "frame"}:
                        raise ValueError(f"Unknown device data type: {data.type}")

                    if data.type == "frame" and not isinstance(data.data, CameraFrame):
                        raise TypeError("Frame data must contain CameraFrame.")
                    if data.type != "frame" and not isinstance(data.data, dict):
                        raise TypeError("Sample and settings data must contain a dictionary.")


                    # 処理待ちの間に現在の測定が変わっても、投入時の時間基準を使う。
                    device_id    = data.id
                    timestamp_ns = data.timestamp_ns
                    elapsed_s    = self._get_elapsed(timestamp_ns, period)
                    datetime     = self._get_datetime(timestamp_ns, period)

                    # 通知をコピーして次の記録開始に備える。今回の記録対象なら履歴とJSONにも反映する。
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

                    # 画像はJPEG生成側へ、数値はイベント配信側へ渡す。保存への受け渡しは後段で行う。
                    if data.type == "frame":
                        if self.camera_preview_service is not None:
                            try:
                                notification = self.latest_settings.get(device_id)
                                if notification is None:
                                    raise RuntimeError(f"No camera settings received for {device_id}.")
                                self.camera_preview_service.offer_frame(
                                    device_id,
                                    data.data,
                                    timestamp_ns,
                                    CameraSetting.model_validate(notification.data),
                                    server_instance_id=self.server_instance_id,
                                    measurement_id=period.measurement_id,
                                )
                            except Exception:
                                # 表示用画像の受け渡しが失敗しても、元の画像を使う保存処理へ進む。
                                self.logger.exception("Could not submit camera preview for %s", device_id)

                    elif self.event_broadcaster.is_running():
                        try:
                            event = MeasurementEvent(
                                server_instance_id=self.server_instance_id,
                                measurement_id=period.measurement_id,
                                id=device_id,
                                timestamp_ns=timestamp_ns,
                                elapsed_s=elapsed_s,
                                datetime=datetime,
                                data=data.data,
                            )
                            # 数値の共通購読先を表すキーNoneへ、JSON対応の辞書を配信する。
                            self.event_broadcaster.publish(event.model_dump(mode="json"), key=None)
                        except Exception:
                            self.logger.exception("Could not publish measurement event for %s", device_id)

                    # DataRecorderは記録開始時の対象IDを持ち、対象外のデータを保存せずに戻す。
                    if self._data_recorder.is_recording():
                        try:
                            self._data_recorder.write(data, elapsed_s=elapsed_s)
                        except Exception as exc:
                            # 保存失敗を終了理由としてDataRecorderへ渡し、別スレッドの最終保存を要求する。
                            self._request_recording_stop(error=exc)
                            raise


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
                # 保存受付を止め、別スレッドでの最終保存を要求する。ここでは完了を待たない。
                self._request_recording_stop()
            finally:
                with self._state_lock:
                    self._processor_thread = None

    def _ensure_not_shutting_down(self) -> None:
        """shutdown開始後ならRuntimeErrorを送出し、新規操作を拒否する。"""
        if self._shutdown_requested.is_set():
            raise RuntimeError("MeasurementManager is shutting down.")

    def _raise_shutdown_errors(self, errors: list[tuple[str, Exception]]) -> None:
        """収集した終了失敗に段階名を付け、例外として呼び出し元へ返す。

        Args:
            errors (list[tuple[str, Exception]]): 終了処理の段階名と、その段階で発生した例外。
                空なら何もせず戻る。

        Raises:
            Exception: 一件だけの場合は段階名の注記を加えた元の例外。
            ExceptionGroup: 複数の場合は、それぞれの元の例外を含むグループ。
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
        """新規操作を拒否し、Queue監視・取得・記録・Processorの順に終了する。

        監視と取得の停止をそれぞれ試み、動作中のものが残れば記録とProcessorを保持して
        失敗を返す。取得停止後は受付済みデータを処理して記録を終了し、保存リソースと
        最終JSONの処理が残っていないことを確認してからProcessorを止める。

        途中で失敗しても、解放済みのものを作り直さずに再度shutdownを呼べる。
        shutdown開始後の取得開始・記録開始・設定更新は拒否したままとする。
        命令や保存完了の待機にはSHUTDOWN_WAIT_TIMEOUTを使い、各スレッドの停止には
        それぞれの待機上限を使うため、関数全体が10秒以内に終わるという意味ではない。

        Raises:
            TimeoutError: 別のshutdownが待機上限内に終わらず、この呼び出しを開始できない場合。
            Exception: 一つの段階の終了に失敗した場合。その段階の注記を付ける。
            ExceptionGroup: 複数の段階で失敗した場合。元の例外をまとめて返す。
        """
        self._shutdown_requested.set()
        if not self._shutdown_lock.acquire(timeout=self.SHUTDOWN_WAIT_TIMEOUT):
            raise TimeoutError("Another measurement shutdown is still running.")
        try:
            errors = []
            # 監視停止に失敗しても、各機器の取得停止は続けて試す。
            for stage, stop in (("queue monitor", self._stop_queue_monitor), ("sampling", self.stop_all_sampling)):
                try:
                    stop()
                except Exception as exc:
                    errors.append((stage, exc))

            # 停止呼び出しの成否に加えて実際の生存状態を調べ、取得が残る間は保存側を終了しない。
            monitor = self._queue_monitor_thread
            monitor_running = monitor is not None and monitor.is_alive()
            sampling = self._is_any_device_sampling()
            if monitor_running or sampling:
                if not errors:
                    errors.append(("acquisition", TimeoutError("Acquisition or queue monitoring has not stopped.")))
                self._raise_shutdown_errors(errors)

            try:
                if self._is_processor_running():
                    # 取得停止後にQueue末尾へ命令を入れることで、それ以前のデータ処理を待つ。
                    # 続けてDataRecorderの動画・CSV・最終JSONの終了結果を確認する。
                    self.stop_recording(timeout=self.SHUTDOWN_WAIT_TIMEOUT)
                else:
                    # Processorがなければ直接終了を要求する。終了スレッドがすでに動いていれば重ねて開始しない。
                    if not self._data_recorder.is_finalizing():
                        self._request_recording_stop()
                    self._data_recorder.wait_stopped(timeout=self.SHUTDOWN_WAIT_TIMEOUT)
            except Exception as exc:
                errors.append(("recording", exc))

            # 保存スレッド・動画プロセス・ファイル・未保存JSONが残る間は、Processorと参照を保持する。
            if self._data_recorder.has_pending_cleanup():
                if not errors:
                    errors.append(("recording", TimeoutError("Recording cleanup has not finished.")))
                self._raise_shutdown_errors(errors)

            if not self._is_processor_running() and self.data_queue.qsize():
                errors.append(("data queue", RuntimeError(
                    f"Processor stopped with {self.data_queue.qsize()} unprocessed queue items."
                )))

            # 保存の後始末が残っていなければ、記録でエラーが出ていてもProcessorを停止できる。
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

    def _get_elapsed(self, counter_ns: int, period: MeasurementPeriod) -> float:
        """データに対応する測定期間を基準に、経過秒を求める。

        Args:
            counter_ns (int): 対象データに付けられたPCのカウンタ時刻。単位はns。
            period (MeasurementPeriod): データの投入元が保持する測定期間。

        Returns:
            float: 共通の取得開始カウンタから対象データまでの経過秒。
        """
        return counter_to_elapsed_seconds(counter_ns, period.start_counter_ns)

    def _get_datetime(self, counter_ns: int, period: MeasurementPeriod):
        """データに対応する測定期間のカウンタと実日時から、取得日時を求める。

        Args:
            counter_ns (int): 対象データに付けられたPCのカウンタ時刻。単位はns。
            period (MeasurementPeriod): データの投入元が保持する測定期間。

        Returns:
            datetime: 取得開始の実日時に、カウンタ差の経過時間を加えた日時。
        """
        return counter_to_datetime(
            counter_ns,
            period.start_counter_ns,
            period.started_at,
        )

