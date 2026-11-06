"""実カメラ用ドライバーのひな型となるダミーカメラ。

共通処理は接続状態、設定通知、DeviceDataへの格納、取得スレッドを扱う。
実機へ置き換える際は、初期化の「デバイス固有の初期値」と、末尾の
「デバイス固有処理」を書き換える。SDKのハンドルも初期化時に追加する。
ROIの切り出しと動画保存は保存側が行うため、このクラスでは行わない。
"""

import threading
import time
from queue import Full, Queue
from typing import Any

from backend.app.models.devices import CameraSetting, DeviceInfo, DiscoveredDevice
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.utils.logging_utils import setup_logger
from backend.app.utils.time_utils import get_counter_ns


class DummyCamera:
    """設定通知と画像を共通Queueへ渡す、SDK不要のカメラ。"""

    STOP_TIMEOUT = 2.0  # 取得スレッドの終了待機上限。SDKの通信タイムアウトとは別。
    DATA_PUT_TIMEOUT = 0.1
    SETTINGS_PUT_TIMEOUT = 1.0

    def __init__(self, device_id: str, serial_number: str = "", address: str = ""):
        """デバイスの識別情報と取得状態を初期化する。

        Args:
            device_id (str): DeviceManagerが生成したデバイスID。
            serial_number (str): 探索で取得したシリアル番号。
            address (str): SDKへ渡す接続先。
        """
        self.device_id = device_id
        self.serial_number = serial_number
        self.address = address
        self.connected = False
        self.last_error: str | None = None
        self._state_lock = threading.RLock()
        self._io_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._sampling_thread: threading.Thread | None = None
        self.data_queue: Queue | None = None
        self.logger = setup_logger(type(self).__name__)

        # デバイス固有の初期値。実機では接続時に読み出した適用値へ更新する。
        self.settings = CameraSetting(width=640, height=480, fps=10, exposure=0.01, gain=1)
        self._frame_index = 0

    # ========================== 共通処理 ==========================

    def connect(self) -> bool:
        """接続し、機器から読み出した撮影設定を保持する。"""
        with self._state_lock:
            if self.last_error:
                raise RuntimeError(self.last_error)
            if self._sampling_thread is not None and self._sampling_thread.is_alive():
                if self._stop_event.is_set():
                    raise RuntimeError("Previous sampling thread has not stopped.")
                return True
            if self.connected:
                return True
            with self._io_lock:
                self.settings = self._open_device().model_copy(deep=True)
                self.connected = True
            return True

    def disconnect(self) -> bool:
        """取得を停止して機器を閉じる。停止・切断失敗時はFalseを返す。"""
        with self._state_lock:
            try:
                self.stop_sampling()
                with self._io_lock:
                    self._close_device()
                    self.connected = False
                    self.last_error = None
                return True
            except Exception as exc:
                self.last_error = str(exc) or type(exc).__name__
                self.logger.exception("Could not disconnect device %s", self.device_id)
                return False

    def update_address(self, address: str) -> None:
        """切断中の接続先を更新する。"""
        with self._state_lock:
            if self.connected:
                raise RuntimeError("Cannot update address while connected.")
            self.address = address

    def get_settings(self) -> CameraSetting:
        """最後に適用した設定のコピーを返す。"""
        with self._io_lock:
            return self.settings.model_copy(deep=True)

    def set_settings(self, settings: CameraSetting | dict[str, Any]) -> CameraSetting:
        """設定一式を検証・適用し、取得中は次のフレームより先に通知する。

        Args:
            settings (CameraSetting | dict[str, Any]): 撮影条件と保存ROIの設定一式。

        Returns:
            CameraSetting: 機器へ適用した設定のコピー。
        """
        values = settings.model_dump() if isinstance(settings, CameraSetting) else settings
        validated = CameraSetting.model_validate(values)
        with self._io_lock:
            self.settings = self._apply_settings(validated).model_copy(deep=True)
            self._put_settings()
            return self.settings.model_copy(deep=True)

    def _put_settings(self) -> None:
        """取得中のQueueへ、適用済み設定とPC側のカウンタ時刻を追加する。"""
        if self.data_queue is not None and not self._stop_event.is_set():
            self._put_data(DeviceData(
                id=self.device_id,
                type="settings",
                timestamp_ns=get_counter_ns(),
                data=self.settings.model_dump(mode="json"),
            ))

    def _put_data(self, data: DeviceData) -> None:
        """期限付きで投入し、満杯の場合は自機の取得を止めて投入失敗を返す。

        Args:
            data (DeviceData): 設定通知または取得データ。設定通知は1秒、取得データは0.1秒待つ。
        """
        timeout = self.SETTINGS_PUT_TIMEOUT if data.type == "settings" else self.DATA_PUT_TIMEOUT
        try:
            self.data_queue.put(data, timeout=timeout)
        except Full:
            self._stop_event.set()
            raise

    def read(self) -> DeviceData:
        """全画面の画像を1枚取得し、PC側の取得時刻を付けて返す。"""
        with self._io_lock:
            if not self.connected:
                raise RuntimeError("Device is not connected.")
            frame = self._read_frame()
            return DeviceData(self.device_id, "frame", get_counter_ns(), frame)

    def start_sampling(
        self, queue: Queue,
    ) -> threading.Thread:
        """設定通知をQueueへ追加してから取得を開始する。取得中なら同じスレッドを返す。

        Args:
            queue (Queue): 取得データの投入先。MeasurementManagerからは満杯通知付きの投入先が渡される。

        Returns:
            threading.Thread: 画像取得スレッド。
        """
        with self._state_lock:
            if self._sampling_thread is not None and self._sampling_thread.is_alive():
                if self._stop_event.is_set() or self.last_error:
                    raise RuntimeError("Previous sampling thread has not stopped.")
                return self._sampling_thread
            if not self.connected or self.last_error:
                raise RuntimeError("Device is not connected.")
            with self._io_lock:
                self.data_queue = queue
                self._stop_event.clear()
                try:
                    self._put_settings()
                except Full:
                    self.data_queue = None
                    raise
            self._sampling_thread = threading.Thread(
                target=self._sample_data, name=f"sampling-{self.device_id}",
            )
            self._sampling_thread.start()
            return self._sampling_thread

    def stop_sampling(self) -> None:
        """取得終了を待ち、Queueへの参照を解除する。終了待機の超過時は例外を送出する。"""
        with self._state_lock:
            self._stop_event.set()
            thread = self._sampling_thread
            if thread is not None:
                thread.join(timeout=self.STOP_TIMEOUT)
                if thread.is_alive():
                    # 参照を残し、停止の再試行と重複起動の拒否に使う。
                    self.last_error = "Timed out waiting for sampling to stop."
                    raise TimeoutError(self.last_error)
                self._sampling_thread = None
            with self._io_lock:
                self.data_queue = None

    def is_sampling(self) -> bool:
        """画像取得スレッドが動作中かどうかを返す。"""
        with self._state_lock:
            return self._sampling_thread is not None and self._sampling_thread.is_alive()

    def _sample_data(self) -> None:
        """取得とQueueへの追加を設定変更と直列化し、設定FPSの周期で繰り返す。"""
        try:
            while not self._stop_event.is_set():
                started = time.perf_counter()
                with self._io_lock:
                    if self._stop_event.is_set():
                        break
                    self._put_data(self.read())
                    interval = 1.0 / self.settings.fps
                self._stop_event.wait(max(0.0, interval - (time.perf_counter() - started)))
        except Full as exc:
            self.logger.error("Sampling stopped for %s: %s", self.device_id, exc)
        except Exception as exc:
            if not self._stop_event.is_set():
                self.connected = False
                self.last_error = str(exc) or type(exc).__name__
                self.logger.exception("Sampling failed for %s", self.device_id)
        finally:
            self._stop_event.set()

    # ========================== デバイス固有処理 ==========================
    # 実機用のクラスでは、以下をSDKの探索・接続・設定・取得処理へ置き換える。

    @classmethod
    def scan(cls) -> list[DiscoveredDevice]:
        """SDKの探索結果に相当する、固定のダミーカメラ1台を返す。"""
        return [DiscoveredDevice(
            driver=cls.__name__, type="camera", manufacturer="DummyManufacturer",
            model="DummyCamera", serial_number="DUMMY_CAMERA_001", address="dummy_camera_1",
        )]

    def get_info(self) -> DeviceInfo:
        """機器の識別情報と、取得エラーを優先した接続状態を返す。"""
        return DeviceInfo(
            id=self.device_id, type="camera", manufacturer="DummyManufacturer",
            model="DummyCamera", serial_number=self.serial_number, address=self.address,
            status="error" if self.last_error else "connected" if self.connected else "disconnected",
        )

    def _open_device(self) -> CameraSetting:
        """SDKで接続し、実際の撮影設定を返す箇所。ダミーでは保持中の設定を返す。"""
        return self.settings

    def _close_device(self) -> None:
        """SDKの取得終了・ハンドル解放を行う箇所。ダミーでは解放する資源はない。"""

    def _apply_settings(self, settings: CameraSetting) -> CameraSetting:
        """SDKで設定を適用し、読み戻した適用値を返す箇所。

        ダミーでは要求値をそのまま返す。露光・ゲイン・ゲインブーストは保持のみで、
        合成画像の明るさには反映しない。roiはソフトウェア保存用として保持する。
        """
        return settings

    def _read_frame(self) -> CameraFrame:
        """SDKから全画面の画像とフレーム番号を取得する箇所。

        実機ではC連続のuint8画像を返し、SDKが再利用するバッファはコピーする。
        SDKの取得待機にはタイムアウトを設定し、停止要求後も戻れるようにする。
        機器の時刻が取れる場合だけdevice_timestamp_nsへns単位で格納する。
        ダミーでは1フレームごとに右へ動く縦線を生成する。
        """
        width, height = self.settings.width, self.settings.height
        x = self._frame_index % width
        if self.settings.pixel_format == "mono8":
            background, line = b"\x10", b"\xc0"
            shape = (height, width)
        else:
            background = b"\x10\x10\x10"
            line = b"\xc0\x60\x20" if self.settings.pixel_format == "rgb24" else b"\x20\x60\xc0"
            shape = (height, width, 3)
        row = background * x + line + background * (width - x - 1)
        # 不変のbytesをフレームごとに作成し、Queueに渡した画像を再利用しない。
        image = memoryview(row * height).cast("B", shape=shape)
        frame = CameraFrame(image=image, frame_index=self._frame_index)
        self._frame_index += 1
        return frame
