""" 
複数デバイスの別スレッドでのデータ取得や保存、解析、
websocket通信などを管理するクラス。

非同期処理自体はdriver側で行う。
MeasurementManagerでは、それらの非同期処理の管理、
全データをself.data_queueに集約し、保存・解析・websocket送信などを行う。
"""

import queue
import threading
from dataclasses import dataclass, field

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
    action: str
    directory: str | None = None
    name: str | None = None
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None


class MeasurementManager:
    """
    複数デバイスのデータ取得や保存、解析、
    websocket通信などを管理するクラス。
    """

    # 共通処理スレッドの終了待機時間
    STOP_TIMEOUT = 2.0

    def __init__(self, device_manager):
        self.device_manager = device_manager

        # threading関連
        self.data_queue = queue.Queue()             # 測定データを受け取る共通Queue
        self._processor_thread = None               # Queueを監視するスレッド
        self._state_lock = threading.RLock()        # APIからの開始・停止処理を同期する
        self._recording_lock = threading.Lock()     # 記録開始・終了命令の重複実行を防ぐ
    
        # 監視スレッドへのシグナル。
        # シグナルをself.data_queueに入れることで、監視スレッドに命令を送る
        self._stop_signal = object()                # Processor終了

        # DataRecorderの初期化
        self._data_recorder = DataRecorder()

        # 測定開始時刻
        self._start_counter_ns: int | None = None
        self._start_datetime = None

        # ロガーの初期化
        self.logger = setup_logger(__class__.__name__)

        # Queue監視スレッドを起動
        self._start_processor()

    # ========================== Sampling ==========================

    def start_sampling(self, device_id: str) -> None:
        """指定されたデバイスのデータ取得を開始する。"""
        with self._state_lock:
            device = self.device_manager.get(device_id)

            # すでに取得中なら何もしない
            if device.is_sampling():
                return

            # Processorが停止していた場合は再起動する
            self._start_processor()

            # 最初のデバイスを開始するときに共通の測定開始時刻を設定する
            if not self._is_any_device_sampling():
                # 前回測定のデータが残っている場合は処理完了を待つ
                self.data_queue.join()
                if not self._data_recorder.is_recording():
                    self._start_counter_ns = get_counter_ns()
                    self._start_datetime = get_datetime()

            try:
                # デバイスのデータ取得を開始する
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
            
            # デバイスのデータ取得を停止する
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
    def start_recording(self, directory: str, name: str | None = None) -> None:
        """記録を開始する。"""
        with self._recording_lock:
            with self._state_lock:
                if not self._is_any_device_sampling():
                    raise RuntimeError("No device is sampling. Cannot start recording.")
                if self._data_recorder.is_recording():
                    raise RuntimeError("Recording has already started.")
                if not self._is_processor_running():
                    raise RuntimeError("Processor is not running.")

                command = RecordingCommand(action="start", directory=directory, name=name)
                self.data_queue.put(command)

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
        """Processorによる記録命令の完了を待つ。"""
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

            # Processorへ終了を通知
            self.data_queue.put(self._stop_signal)

        # Lockを保持したままjoinしない
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
                    # ------------ recording ------------
                    # 保存開始・終了命令
                    if isinstance(data, RecordingCommand):
                        try:
                            if data.action == "start":
                                self._data_recorder.start(data.directory, data.name)
                            elif data.action == "stop":
                                self._data_recorder.stop()
                            else:
                                raise ValueError(f"Unknown recording action: {data.action}")
                        except Exception as exc:
                            data.error = exc
                        finally:
                            data.done.set()
                        continue

                    # 保存・解析・WebSocket送信など
                    # self._process_data(data)

                    device_id = data.id
                    timestamp_ns = data.timestamp_ns
                    elapsed_s = self._get_elapsed(timestamp_ns)
                    datetime = self._get_datetime(timestamp_ns)

                    # 保存中の場合はDataRecorderにデータを渡す
                    if self._data_recorder.is_recording():
                        self._data_recorder.write(data, elapsed_s=elapsed_s)

                    self.logger.debug(
                        "Processed data: id=%s timestamp=%s elapsed=%s datetime=%s data=%s",
                        device_id,
                        timestamp_ns,
                        elapsed_s,
                        datetime,
                        data,
                    )

                except Exception:
                    # 1点の処理失敗でProcessor全体を停止させない
                    self.logger.exception("Could not process measurement sample")

                finally:
                    self.data_queue.task_done()

        finally:
            try:
                # 保存中にProcessorが終了した場合は、保存を終了する
                self._data_recorder.stop()
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
