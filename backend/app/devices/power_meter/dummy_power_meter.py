"""実パワーメーター用ドライバーのひな型となるダミーパワーメーター。

共通処理は接続状態、設定通知、DeviceDataへの格納、取得スレッドを扱う。
実機へ置き換える際は、初期化の「デバイス固有の初期値」と、末尾の
「デバイス固有処理」を書き換える。SDKのハンドルも初期化時に追加する。
"""

import math
import time
import threading
from queue import Full
from typing import Any, Callable

# データ型
from backend.app.models.devices import DeviceInfo, DiscoveredDevice, PowerMeterSettings
from backend.app.models.measurement import DeviceData

# ユーティリティ
from backend.app.utils.time_utils import get_counter_ns
from backend.app.utils.logging_utils import setup_logger


class DummyPowerMeter:
    # joinの待機上限。通信処理そのものを中断するタイムアウトではない。
    STOP_TIMEOUT = 2.0
    DATA_PUT_TIMEOUT = 0.1
    SETTINGS_PUT_TIMEOUT = 1.0

    def __init__(self, 
                device_id: str,
                serial_number: str = "",
                address: str = ""):
        """ 
        デバイスを初期化する。

        Args:
            device_id (str): デバイスID。
            serial_number (str): デバイスのシリアル番号。
            address (str): デバイスのアドレス。
        """
        self.device_id = device_id
        self.serial_number = serial_number
        self.address = address
        self.connected = False                  # デバイスが接続されているかどうか
        self.last_error: str | None = None      # 接続フラグだけでは表せない、取得失敗・停止タイムアウトの原因を保持する。

        # threading関連
        self._stop_event = threading.Event()    # データ取得停止フラグ
        self._sampling_thread = None            # データ取得スレッド
        self._state_lock = threading.RLock()    # 取得開始・停止と接続状態の変更が、別のAPI要求で重ならないようにする。
        self._io_lock = threading.RLock()       # データ取得・設定変更とQueueへの追加を順番に実行する。
        self.data_queue = None                 # サンプリング中に使用する共通Queue。
        self._on_queue_full: Callable[[str], None] | None = None

        self.logger = setup_logger(__class__.__name__)

        # デバイス固有の初期値。実機では接続時に読み出した適用値へ更新する。
        self.settings = PowerMeterSettings(
            wavelength=550.0,
            range=1.0,
            auto_range=True,
            bandwidth="high",
            averaging=10
        )

    # ========================== 共通処理 ==========================

    def connect(self) -> bool:
        """デバイスに接続する。

        Returns:
            return (bool): 接続成功ならTrue、失敗ならFalse。
        """
        with self._state_lock:
            # データ取得スレッドがまだ存在 and 動作中
            if self._sampling_thread is not None and self._sampling_thread.is_alive():
                # 停止命令が出ているのにスレッドが生きている or エラーが出ている場合は、接続を拒否する。
                if self._stop_event.is_set() or self.last_error:
                    raise RuntimeError("Previous sampling thread has not stopped.")
                
                # 正常な取得中なら、接続済みとしてTrueを返す。
                return True

            # エラーが出ている場合、接続を拒否する。
            if self.last_error:
                raise RuntimeError(self.last_error)
            
            # 接続済みなら、再接続せずTrueを返す。
            if self.connected:
                return True

            with self._io_lock:
                self.settings = self._open_device()
                self.last_error = None
                self.connected = True
            return True

    def disconnect(self) -> bool:
        """デバイスとの接続を切断する。

        Returns:
            return (bool): 切断成功ならTrue、失敗ならFalse。
        """
        with self._state_lock:
            try:
                self.stop_sampling()        # sampling threadを停止する

                with self._io_lock:
                    self._close_device()
                    self.connected = False
                    self.last_error = None
                return True
            except Exception as exc:
                # 切断処理に失敗した場合は、エラー内容を記録する。
                self.last_error = str(exc) or type(exc).__name__
                self.logger.exception("Could not disconnect device %s", self.device_id)
                return False
            
    def update_address(self, address: str) -> None:
        with self._state_lock:
            if self.connected:
                raise RuntimeError("Cannot update address while connected.")

            self.address = address

    # ..........................................

    def get_settings(self) -> PowerMeterSettings:
        """現在の設定を取得する。

        Returns:
            return (PowerMeterSettings): 現在の設定。
        """
        with self._io_lock:
            return self.settings

    def set_settings(
        self,
        settings: PowerMeterSettings | dict[str, Any],
    ) -> PowerMeterSettings:
        """新しい設定を適用する。

        Args:
            settings (PowerMeterSettings | dict[str, Any]): 新しい設定一式。

        Returns:
            return (PowerMeterSettings): 適用後の設定。
        """
        # 辞書を設定モデルへ変換し、必須項目と値の型を検証する。
        validated = PowerMeterSettings.model_validate(settings)
        with self._io_lock:
            # 適用後の設定を保持し、サンプリング中は設定通知をQueueへ追加する。
            self.settings = self._apply_settings(validated)
            self._put_settings()
            return self.settings

    def _put_settings(self) -> None:
        """サンプリング中のQueueへ、現在の設定とカウンタ時刻を追加する。"""
        if self.data_queue is not None and not self._stop_event.is_set():
            self._put_data(DeviceData(
                id=self.device_id,
                type="settings",
                timestamp_ns=get_counter_ns(),
                data=self.settings.model_dump(mode="json"),
            ))

    def _put_data(self, data: DeviceData) -> None:
        """期限付きで投入し、満杯の場合は自機の取得を止めて管理側へ通知する。

        Args:
            data (DeviceData): 設定通知または取得データ。設定通知は1秒、取得データは0.1秒待つ。
        """
        timeout = self.SETTINGS_PUT_TIMEOUT if data.type == "settings" else self.DATA_PUT_TIMEOUT
        try:
            self.data_queue.put(data, timeout=timeout)
        except Full as exc:
            self._stop_event.set()
            if self._on_queue_full is not None:
                self._on_queue_full(self.device_id)
            message = (
                "Settings were applied, but the acquisition queue is full; settings notification was not queued."
                if data.type == "settings" else "Acquisition queue is full; sample was not queued."
            )
            raise Full(message) from exc

    # ..........................................
    
    def read(self) -> DeviceData:
        """
        パワー値を取得する。
        """
        with self._io_lock:
            if self.connected is False:
                raise RuntimeError("Device is not connected.")

            sample = self._read_sample()
            return DeviceData(
                id=self.device_id,
                type="sample",
                timestamp_ns=get_counter_ns(),
                data=sample,
            )

    # ..........................................

    def start_sampling(
        self, queue, *, on_queue_full: Callable[[str], None] | None = None,
    ) -> threading.Thread:
        """
        別スレッドでデータ取得を開始する。

        Args:
            queue: 測定データを格納するキュー。
            on_queue_full (Callable[[str], None] | None): 満杯時に機器IDを通知する関数。

        Returns:
            return (threading.Thread): データ取得スレッド。
        """
        with self._state_lock:
            # データ取得スレッドがまだ存在 and 動作中の場合は、現在のスレッドを返す。
            if self._sampling_thread is not None and self._sampling_thread.is_alive():
                if self._stop_event.is_set() or self.last_error:
                    # stop_sampling()が失敗し、スレッドが存在している場合のエラー
                    raise RuntimeError("Previous sampling thread has not stopped.")
                return self._sampling_thread    # スレッドを返す

            if not self.connected or self.last_error:
                raise RuntimeError("Device is not connected.")

            # 共通Queueを保持し、最初の取得データより前に現在の設定を追加する。
            with self._io_lock:
                self.data_queue = queue
                self._on_queue_full = on_queue_full
                self._stop_event.clear()
                try:
                    self._put_settings()
                except Full:
                    self.data_queue = None
                    self._on_queue_full = None
                    raise

            self._sampling_thread = threading.Thread(
                target=self._sample_data,
                name=f"sampling-{self.device_id}",
            )
            self._sampling_thread.start()
            return self._sampling_thread

    def stop_sampling(self) -> None:
        """
        データ取得を停止する。
        """
        with self._state_lock:
            self._stop_event.set()
            thread = self._sampling_thread

            if thread is not None:
                thread.join(timeout=self.STOP_TIMEOUT)
                # タイムアウトしても参照を消さず、停止の再試行と重複起動防止に使う。
                if thread.is_alive():
                    self.last_error = "Timed out waiting for sampling to stop."
                    raise TimeoutError(self.last_error)

                self._sampling_thread = None

            with self._io_lock:
                self.data_queue = None
                self._on_queue_full = None

    def is_sampling(self) -> bool:
        """
        データ取得中かどうかを返す。

        Returns:
            return (bool): データ取得中ならTrue、停止中ならFalse。
        """
        with self._state_lock:
            thread = self._sampling_thread
            return thread is not None and thread.is_alive()

    def _sample_data(self) -> None:
        """
        データ取得スレッドの処理。
        """
        try:
            while not self._stop_event.is_set():
                # セッティング変更とデータ取得の不整合を避けるため、
                # データ取得からQueueへの追加までを同じI/O用Lock内で実行する。
                with self._io_lock:
                    if self._stop_event.is_set():
                        break
                    sample = self.read()
                    if sample is None:
                        raise ValueError("Device returned no sample.")
                    self._put_data(sample)
                self._stop_event.wait(0.1)
                
        except Full as exc:
            self.logger.error("Sampling stopped for %s: %s", self.device_id, exc)
        except Exception as exc:
            # 停止要求によって通信が中断された場合は、通信異常と区別する。
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
        """SDKの探索結果に相当する、固定のダミーパワーメーター2台を返す。"""
        return [
            DiscoveredDevice(
                driver=cls.__name__, type="power_meter", manufacturer="DummyManufacturer",
                model="DummyModel", serial_number="DUMMY123456", address="dummy_address_1",
            ),
            DiscoveredDevice(
                driver=cls.__name__, type="power_meter", manufacturer="DummyManufacturer",
                model="DummyModel", serial_number="DUMMY123457", address="dummy_address_2",
            ),
        ]

    def get_info(self) -> DeviceInfo:
        """機器の識別情報と、取得エラーを優先した接続状態を返す。"""
        return DeviceInfo(
            id=self.device_id, type="power_meter", manufacturer="DummyManufacturer",
            model="DummyModel", serial_number=self.serial_number, address=self.address,
            status="error" if self.last_error else "connected" if self.connected else "disconnected",
        )

    def _open_device(self) -> PowerMeterSettings:
        """SDKで接続し、実際の測定設定を返す箇所。ダミーでは保持中の設定を返す。"""
        return self.settings

    def _close_device(self) -> None:
        """SDKのハンドル解放を行う箇所。ダミーでは解放する資源はない。

        未接続・切断済みでも呼ばれるため、実機では存在するハンドルだけを解放する。
        """

    def _apply_settings(self, settings: PowerMeterSettings) -> PowerMeterSettings:
        """SDKで設定を適用し、読み戻した適用値を返す箇所。

        Args:
            settings (PowerMeterSettings): 検証済みの設定一式。ダミーではそのまま返す。

        Returns:
            PowerMeterSettings: 機器へ適用された設定。要求値が丸められる場合は実際の値。
        """
        return settings

    def _read_sample(self) -> dict[str, object]:
        """SDKで測定値を取得する箇所。ダミーでは振幅1 Wのサイン波を返す。

        実機では取得待機にタイムアウトを設定し、停止要求後も戻れるようにする。

        Returns:
            dict[str, object]: 測定項目名と値。power_wはワット単位のパワー。
        """
        self.amplitude = 1.0
        return {"power_w": self.amplitude * math.sin(time.perf_counter())}
