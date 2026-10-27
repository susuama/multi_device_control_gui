""" 
複数デバイスの別スレッドでのデータ取得や保存、解析、
websocket通信などを管理するクラス。

非同期処理自体はdriver側で行う。
MeasurementManagerでは、それらの非同期処理の管理、
全データをself.data_queueに集約し、保存・解析・websocket送信などを行う。

self.data_queue を監視する processorスレッドがある
start_recording()が実行されるとデータをDataRecorder()クラスに渡す。
DataRecorder()は、このprocessorスレッドから呼び出されてファイルへ書き込む。
"""

import queue
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime

from backend.app.models.events import MeasurementEvent
from backend.app.models.measurement import DeviceData
from backend.app.services.data_racorder import DataRecorder

from backend.app.utils.time_utils import (
    get_counter_ns,
    get_datetime,
    counter_to_elapsed_seconds,
    counter_to_datetime,
)
from backend.app.utils.logging_utils import setup_logger


@dataclass
class RecordingCommand:
    """Queueに渡す記録開始・終了命令。"""
    action: str                         # start / stop : 記録の開始・終了
    directory: str | None = None        # 保存先フォルダ
    name: str | None = None             # 保存フォルダ名
    device_ids: tuple[str, ...] = ()    # 記録開始命令を送った時点で取得中のデバイスID
    done: threading.Event = field(default_factory=threading.Event)  # 命令完了フラグ
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
    devices: dict[str, list[dict[str, object]]] = field(default_factory=dict)


class MeasurementManager:
    """
    複数デバイスのデータ取得や保存、解析、
    websocket通信などを管理するクラス。
    """

    # 共通処理スレッドの終了待機時間
    STOP_TIMEOUT = 2.0

    def __init__(self, device_manager, event_broadcaster) -> None:
        self.device_manager    = device_manager
        self.event_broadcaster = event_broadcaster

        # threading関連
        self.data_queue = queue.Queue()             # 測定データを受け取る共通Queue
        self.latest_settings: dict[str, DeviceData] = {}  # 記録開始時の初期状態に使う最新の設定通知
        self._processor_thread = None               # Queueを監視するスレッド
        self._state_lock = threading.RLock()        # APIからの開始・停止処理を同期する
        self._recording_lock = threading.Lock()     # 記録開始・終了命令の重複実行を防ぐ
        self._metadata_lock = threading.Lock()      # メタデータの読み取りと履歴への追記を同期する
    
        # 監視スレッドへのシグナル。
        # シグナルをself.data_queueに入れることで、監視スレッドに命令を送る
        self._stop_signal = object()                # Processor終了

        # DataRecorderの初期化
        self._data_recorder = DataRecorder()

        # メタデータの初期化
        self._metadata = Metadata()

        # サンプリング開始時刻。elapsed_sの基準になる。
        self._start_counter_ns: int | None = None
        self._start_datetime = None

        self.logger = setup_logger(__class__.__name__)

        # Queue監視スレッドを起動
        self._start_processor()

    # ========================== metadata 管理 ==========================

    def get_metadata(self) -> Metadata:
        """保持している記録情報とデバイス履歴のコピーを返す。"""
        with self._metadata_lock:
            return deepcopy(self._metadata)

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

    def _initialize_metadata(self, device_ids: tuple[str, ...]) -> None:
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
            )

        # 開始命令より前に受信した最新の設定通知を、初期状態として追加する。
        for device_id in device_ids:
            notification = self.latest_settings.get(device_id)
            if notification is None:
                raise RuntimeError(f"No settings received for device {device_id}.")
            self._add_device_metadata(
                device_id, notification.timestamp_ns, settings=notification.data,
            )

    def _save_metadata(self) -> None:
        """メタデータをJSON対応の辞書へ変換し、記録フォルダへ保存する。"""
        with self._metadata_lock:
            metadata = asdict(self._metadata)

        # 日時をタイムゾーン付きのISO形式へ変換し、未設定の値はNoneのまま渡す。
        for key, value in metadata.items():
            if isinstance(value, datetime):
                metadata[key] = value.isoformat()
        self._data_recorder.write_metadata(metadata)

    def _finish_recording(self, error: Exception | None = None) -> None:
        """記録を停止し、終了日時を含むメタデータを保存する。"""
        if error is not None:
            self._data_recorder.error = str(error) or type(error).__name__

        with self._metadata_lock:
            # メタデータの終了時刻が未設定かどうか。未設定の場合だけ今回の終了時刻を保存する。
            save_metadata = (
                self._metadata.recording_started_at is not None
                and self._metadata.recording_stopped_at is None
            )
            if save_metadata:
                self._metadata.recording_stopped_at = get_datetime()

        try:
            self._data_recorder.stop()
        finally:
            # CSVを閉じた後に、今回の記録の最終メタデータを保存する。
            if save_metadata:
                self._save_metadata()


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
            device = self.device_manager.get(device_id)

            if device.is_sampling():
                return
            self._start_processor()     # Processorが停止していた場合は再起動する

            # 最初のデバイスを開始時。共通のサンプリング開始時刻を設定する
            if not self._is_any_device_sampling():
                # 前回測定のデータが残っている場合は処理完了を待つ
                self.data_queue.join()
                if not self._data_recorder.is_recording():
                    self._start_counter_ns = get_counter_ns()
                    self._start_datetime = get_datetime()
    
            try:
                device.start_sampling(self.data_queue)
            except Exception:
                # 最初のデバイスの開始に失敗した場合は基準時刻を破棄する
                if not self._is_any_device_sampling() and not self._data_recorder.is_recording():
                    self._start_counter_ns = None
                    self._start_datetime = None

                self.logger.exception("Could not start sampling for device %s", device_id)
                raise

    def stop_sampling(self, device_id: str) -> None:
        """指定されたデバイスのデータ取得を停止する。"""
        with self._state_lock:
            device = self.device_manager.get(device_id)
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

    # ========================== recording ==========================
    # サンプリング中のデータを保存する
    # 一つのフォルダ内にメタデータとデバイスごとのデータcsvを保存する。
    #
    # ここでは、保存開始・停止の命令をqueue監視スレッドへ送るだけ
    # 保存処理はDataRecorder()クラスに委譲する。

    def start_recording(self, directory: str, name: str | None = None) -> None:
        """記録を開始する。"""
        with self._recording_lock:
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
                if not self._is_processor_running():
                    raise RuntimeError("Processor is not running.")

                # queueを通じてqueue監視スレッドへ記録開始と設定の命令を送る
                command = RecordingCommand(
                    action="start", directory=directory, name=name, device_ids=device_ids,
                )
                self.data_queue.put(command)

            # 記録が開始するまで待機する
            self._wait_recording_command(command)

    def stop_recording(self) -> None:
        """記録を終了する。"""
        with self._recording_lock:
            if not self._is_processor_running():
                raise RuntimeError("Processor is not running.")
            command = RecordingCommand(action="stop")
            self.data_queue.put(command)
            self._wait_recording_command(command)

    def _wait_recording_command(self, command: RecordingCommand) -> None:
        """
        Processorによる記録命令の完了を待つ。
        
        RecordingCommandデータクラスのdoneにはスレッドイベントオブジェクトが格納される
        queue監視スレッド内で命令が完了し、このフラグが立つのを待つ
        """
        while not command.done.wait(timeout=0.5):
            if not self._is_processor_running():
                raise RuntimeError("Processor stopped before completing recording command.")
        if command.error is not None:
            raise command.error

    def is_recording(self) -> bool:
        """記録中かどうかを返す。"""
        with self._state_lock:
            return self._data_recorder.is_recording()

    def get_recording_status(self):
        """記録状態を返す。"""
        with self._state_lock:
            return self._data_recorder.get_status()

    # ========================== Processor ==========================
    # queue監視スレッド
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
        """Queue監視スレッドを起動する。すでに起動している場合は何もしない。"""
        if self._is_processor_running():
            return

        self._processor_thread = threading.Thread(
            target=self._processor_loop,
            name="measurement-processor",
        )
        self._processor_thread.start()

    def _stop_processor(self) -> None:
        """Queue監視スレッドを終了する。"""
        with self._state_lock:
            thread = self._processor_thread
            if thread is None or not thread.is_alive():
                self._processor_thread = None
                return

            self.data_queue.put(self._stop_signal)      # Processorへ終了を通知

        # Processorの終了処理でも_state_lockを取得するため、ロック外でjoinする
        thread.join(timeout=self.STOP_TIMEOUT)

        if thread.is_alive():
            raise TimeoutError("Timed out waiting for data processing to stop.")

    def _is_processor_running(self) -> bool:
        """Queue監視スレッドが起動中かどうかを返す。"""
        with self._state_lock:
            return self._processor_thread is not None and self._processor_thread.is_alive()

    def _processor_loop(self) -> None:
        """Queueを監視し、取得された測定データを処理する。"""
        try:
            while True:
                data = self.data_queue.get()

                try:
                    # Processor終了のシグナルを受け取った場合はループを抜ける
                    if data is self._stop_signal:
                        break
                    
                    # recordingの保存開始・終了命令の処理。
                    if isinstance(data, RecordingCommand):
                        try:
                            if data.action == "start":
                                self._data_recorder.start(data.directory, data.name)
                                try:
                                    # 今回の初期状態を作成し、最初のサンプル保存前にJSONを作成する。
                                    self._initialize_metadata(data.device_ids)
                                    self._save_metadata()
                                except Exception as exc:
                                    self._finish_recording(error=exc)
                                    raise
                            elif data.action == "stop":
                                self._finish_recording()
                            else:
                                raise ValueError(f"Unknown recording action: {data.action}")
                        except Exception as exc:
                            data.error = exc
                        finally:
                            data.done.set()     # 処理完了フラグを立てる
                        continue

                    # デバイスからの取得データ・設定通知を確認
                    if not isinstance(data, DeviceData):
                        raise TypeError("Expected DeviceData from device.")
                    if data.type not in {"sample", "settings"}:
                        raise ValueError(f"Unknown device data type: {data.type}")

                    # ====================================================

                    # データを整形
                    device_id    = data.id
                    timestamp_ns = data.timestamp_ns
                    elapsed_s    = self._get_elapsed(timestamp_ns)
                    datetime     = self._get_datetime(timestamp_ns)

                    # デバイスの設定変更。
                    # device_idごとに最新の設定を保持。recording中はメタデータへの追記とJSONの更新を行う。
                    if data.type == "settings":
                        self.latest_settings[device_id] = deepcopy(data)
                        if self._data_recorder.is_recording():
                            try:
                                self._add_device_metadata(device_id, timestamp_ns, settings=data.data)
                                self._save_metadata()
                            except Exception as exc:
                                self._finish_recording(error=exc)
                                raise
                        self.logger.debug(
                            "Received settings: id=%s timestamp=%s elapsed=%s datetime=%s settings=%s",
                            device_id, timestamp_ns, elapsed_s, datetime, data.data,
                        )
                        continue

                    # ====== 記録中。DataRecorderにデータを渡す ======
                    if self._data_recorder.is_recording():
                        try:
                            self._data_recorder.write(data, elapsed_s=elapsed_s)
                        except Exception as exc:
                            # CSVの保存に失敗した場合も、終了日時をメタデータへ保存する。
                            self._finish_recording(error=exc)
                            raise

                    # ====== Websocket ======
                    if self.event_broadcaster.is_running():
                        event = MeasurementEvent(
                            id=device_id,
                            timestamp_ns=timestamp_ns,
                            elapsed_s=elapsed_s,
                            datetime=datetime,
                            data=data.data,
                        )
                        # eventモデルをJSON対応の辞書へ変換し、配信処理へ渡す。
                        self.event_broadcaster.publish(event.model_dump(mode="json"))

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
                # Processorの終了時にも、CSVを閉じて最終メタデータを保存する。
                self._finish_recording()
            finally:
                with self._state_lock:
                    self._processor_thread = None

    # ========================== Shutdown ==========================

    def shutdown(self) -> None:
        """すべてのデータ取得とProcessorを終了する。"""
        self.stop_all_sampling()

        # DeviceがすでにQueueへ送ったデータを最後まで処理する
        self.data_queue.join()
        self._stop_processor()

        self._start_counter_ns = None
        self._start_datetime = None

        self.logger.info("MeasurementManager has been shutdown.")

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
