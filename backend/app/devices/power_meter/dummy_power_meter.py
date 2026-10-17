""" 
ダミーの実装

必須変数
- driver_name (str) : デバイスドライバーの識別名。クラス名。
                      scan()で返すDiscoveredDeviceのdriverに対応する。
                      DEVICE_CLASSESのキーとしても使用される。

必須メソッド
- scan(cls)     (@classmethod)

- connect()
- disconnect()
- update_address(address)

- get_info()
- get_settings()
- set_settings(settings)

- read()
- start_sampling(queue, on_stopped=None)
- stop_sampling()

必須データ型
- DiscoveredDevice          scan()の返り値の型
- DeviceInfo                get_info()の返り値の型
- (device-type)Settings     settingsの型。デバイスタイプごとに定義する。

- (device-type)Sample       read()の返り値の型。デバイスタイプごとに定義する。

データの取得時間は get_counter_ns() を用いる
from backend.app.utils.time_utils import get_counter_ns
"""
import math
import time
import threading

# データ型
from backend.app.models.devices import DeviceInfo, DiscoveredDevice, PowerMeterSettings
from backend.app.models.measurement import PowerSample

# ユーティリティ
from backend.app.utils.time_utils import get_counter_ns
from backend.app.utils.logging_utils import setup_logger




class DummyPowerMeter:
    # joinの待機上限。通信処理そのものを中断するタイムアウトではない。
    STOP_TIMEOUT = 2.0

    @classmethod
    def scan(cls) -> list[DiscoveredDevice]:
        """
        デバイスを検索する

        Returns:
            return (list[DiscoveredDevice]): 検出されたデバイスの情報のリスト。
        """
        # ドライバーの識別名（クラス名）
        driver_name = cls.__name__  

        # デバイススキャンの処理
        # ...

        # ここでは、ダミーのデバイス情報を返す
        return [
            DiscoveredDevice(
                driver=driver_name,
                type="power_meter",
                manufacturer="DummyManufacturer",
                model="DummyModel",
                serial_number="DUMMY123456",
                address="dummy_address_1"
            ),
            DiscoveredDevice(
                driver=driver_name,
                type="power_meter",
                manufacturer="DummyManufacturer",
                model="DummyModel",
                serial_number="DUMMY123457",
                address="dummy_address_2"
            )
        ]
    
    # ..........................................

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

        # ロガー
        self.logger = setup_logger(__class__.__name__)

        # デバイス設定の初期化
        self.settings = PowerMeterSettings(
            wavelength=550.0,
            range=1.0,
            auto_range=True,
            bandwidth="high",
            averaging=10
        )

    # ..........................................

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

            # ==========================
            # 実際の接続処理
            # 例:
            # self.device = PM160(self.address)
            # self.device.open()


            # 接続完了
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

                # ==========================
                # 実際のデバイス切断処理
                # 例:
                # self.opm.close()

                self.connected = False      # 切断済み
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

    def get_info(self) -> DeviceInfo:
        """機器情報を取得する。

        Returns:
            return (DeviceInfo): 現在の機器情報。
        """
        # ==========================
        # デバイス情報の取得処理
        # ...

        # ここでは、ダミーのデバイス情報を返す
        return DeviceInfo(
            id=self.device_id,
            type="power_meter",
            manufacturer="DummyManufacturer",
            model="DummyModel",
            serial_number=self.serial_number,
            address=self.address,
            # 停止タイムアウトではconnectedがTrueでも、状態はerrorを優先する。
            status="error" if self.last_error else "connected" if self.connected else "disconnected"
        )
    
    def get_settings(self) -> PowerMeterSettings:
        """現在の設定を取得する。

        Returns:
            return (PowerMeterSettings): 現在の設定。
        """
        # ==========================
        # デバイス設定の取得処理
        # ...

        # ここでは、ダミーのデバイス設定を返す
        return self.settings

    def set_settings(
        self,
        settings: PowerMeterSettings,
    ) -> PowerMeterSettings:
        """新しい設定を適用する。

        Args:
            settings (PowerMeterSettings): 新しい設定。

        Returns:
            return (PowerMeterSettings): 適用後の設定。
        """
        self.settings = settings
        return self.settings

    # ..........................................
    
    def read(self) -> PowerSample:
        """
        パワー値を取得する。
        """
        if self.connected is False:
            raise RuntimeError("Device is not connected.")
        
        self.amplitude = 1.0
        power_w = self.amplitude * math.sin(time.perf_counter())        # デモ用のサイン波を返す
        timestamp_ns = get_counter_ns()

        p = PowerSample(
            id=self.device_id,
            timestamp_ns=timestamp_ns, 
            power_w=power_w
        )

        return p

    # ..........................................

    def start_sampling(self, queue) -> threading.Thread:
        """
        別スレッドでデータ取得を開始する。

        Args:
            queue: 測定データを格納するキュー。

        Returns:
            return (threading.Thread): データ取得スレッド。
        """
        with self._state_lock:
            # データ取得スレッドがまだ存在 and 動作中
            if self._sampling_thread is not None and self._sampling_thread.is_alive():
                # stop_sampling()が失敗し、スレッドが存在している場合のエラー
                if self._stop_event.is_set() or self.last_error:
                    raise RuntimeError("Previous sampling thread has not stopped.")
                return self._sampling_thread    # スレッドを返す

            # 接続していない or エラーが出ている なら取得開始を拒否する
            if not self.connected or self.last_error:
                raise RuntimeError("Device is not connected.")

            # データ取得スレッドを開始
            self._stop_event.clear()
            self._sampling_thread = threading.Thread(
                target=self._sample_data,
                args=(queue, ),
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

            # スレッドが存在するか
            if thread is not None:
                thread.join(timeout=self.STOP_TIMEOUT)
                # タイムアウトしたか
                if thread.is_alive():
                    # タイムアウトしても参照を消さず、停止の再試行と重複起動防止に使う。
                    self.last_error = "Timed out waiting for sampling to stop."
                    raise TimeoutError(self.last_error)

                self._sampling_thread = None

    def is_sampling(self) -> bool:
        """
        データ取得中かどうかを返す。

        Returns:
            return (bool): データ取得中ならTrue、停止中ならFalse。
        """
        with self._state_lock:
            thread = self._sampling_thread
            return thread is not None and thread.is_alive()

    def _sample_data(self, queue) -> None:
        """
        データ取得スレッドの処理。
        """
        try:
            while not self._stop_event.is_set():
                # データ取得
                sample = self.read()

                # データなしの場合、エラーにする
                if sample is None:
                    raise ValueError("Device returned no sample.")
                queue.put(sample)
                self._stop_event.wait(0.1)
                
        except Exception as exc:
            # 停止要求によって通信が中断された場合は、通信異常と区別する。
            if not self._stop_event.is_set():
                self.connected = False
                self.last_error = str(exc) or type(exc).__name__
                self.logger.exception("Sampling failed for %s", self.device_id)
        finally:
            self._stop_event.set()
