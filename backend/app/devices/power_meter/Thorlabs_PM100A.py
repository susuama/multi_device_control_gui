
import os
import time
import math
import threading
from queue import Full
from typing import Any
import numpy as np
import pandas as pd

from pylablib.devices.Thorlabs.misc import GenericPM
import pyvisa


# データ型
from backend.app.models.devices import DeviceInfo, DiscoveredDevice, PowerMeterSettings
from backend.app.models.measurement import DeviceData

# ユーティリティ
from backend.app.utils.time_utils import get_counter_ns
from backend.app.utils.logging_utils import setup_logger


class PM100A:
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

        self.pm100a: GenericPM | None = None
        self._device_info = None

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
                reason = str(exc) or type(exc).__name__
                self.last_error = f"{self.last_error}; 切断失敗: {reason}" if self.last_error else reason
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
        self, queue,
    ) -> threading.Thread:
        """
        別スレッドでデータ取得を開始する。

        Args:
            queue: 測定データを格納するキュー。

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
                self._stop_event.clear()
                try:
                    self._put_settings()
                except Full:
                    self.data_queue = None
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
                    message = "Timed out waiting for sampling to stop."
                    self.last_error = self.last_error or message
                    raise TimeoutError(message)

                self._sampling_thread = None

            with self._io_lock:
                self.data_queue = None

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
                self.last_error = self.last_error or str(exc) or type(exc).__name__
                self.logger.exception("Sampling failed for %s", self.device_id)
                report_error = getattr(self.data_queue, "report_error", None)
                if report_error is not None:
                    report_error(self.device_id, self.last_error)
        finally:
            self._stop_event.set()

    # ========================== デバイス固有処理 ==========================
    # 実機用のクラスでは、以下をSDKの探索・接続・設定・取得処理へ置き換える。

    @classmethod
    def scan(cls) -> list[DiscoveredDevice]:
        """USB接続されているPM100Aデバイスをスキャンする"""
        return [
            DiscoveredDevice(
                driver=cls.__name__, 
                type="power_meter", 
                manufacturer="Thorlabs",
                model="PM100A", 
                serial_number="", 
                address=resource,
            )
            for resource in list(pyvisa.ResourceManager().list_resources())
            if _is_thorlabs_pm100a(resource)
        ]

    def _open_device(self) -> PowerMeterSettings:
        """PM100Aに接続し、パワーモードの現在の設定を返す。"""
        resource = self.address
        pm100a = GenericPM(resource)

        try:
            if not pm100a.is_opened():
                raise ConnectionError(f"PM100Aに接続できませんでした: {self.address}")
            
            info = pm100a.get_device_info()
            if info.name.strip().upper() != "PM100A":
                raise ValueError(f"PM100A以外の機器が指定されています: {info.name}")

            self.pm100a = pm100a
            pm100a.set_sensor_mode("power")
            settings = self._get_settings()

        except:
            pm100a.close()
            self.pm100a = None
            raise

        self._device_info = info
        self.serial_number = info.serial

        self.logger.debug(f"PM100A {resource} is successfully connected.")

        return settings
    
    def is_connected(self) -> bool:
        """識別問い合わせに応答するかを、読み取り待機上限1秒で確認する。"""
        with self._io_lock:
            if self.pm100a is None or not self.pm100a.is_opened():
                return False
            return bool(self.pm100a.get_id(timeout=1.0))

    def get_info(self) -> DeviceInfo:
        """接続を確認し、識別情報と保持中のエラーを返す。"""
        with self._state_lock, self._io_lock:
            if self.connected:
                try:
                    if not self.is_connected():
                        raise ConnectionError("機器との接続が失われました。")
                except Exception as exc:
                    self.connected = False
                    self.last_error = self.last_error or str(exc) or type(exc).__name__
                    self.logger.exception("Connection check failed for %s", self.device_id)
        info = self._device_info

        return DeviceInfo(
            id=self.device_id,
            type="power_meter",
            manufacturer=info.manufacturer if info else "Thorlabs",
            model=info.name if info else "PM100A",
            serial_number=info.serial if info else self.serial_number,
            address=self.address,
            status="error" if self.last_error else "connected" if self.connected else "disconnected",
            error=self.last_error,
        )

    def _get_settings(self) -> PowerMeterSettings:
        """ デバイスの現在の設定を取得します。"""
        if self.pm100a is None or not self.pm100a.is_opened():
            raise ConnectionError("PM100Aに接続されていません")
        
        wavelength = self.pm100a.get_wavelength()
        power_range = self.pm100a.get_range(sensor_mode="power")
        auto_range = self.pm100a.is_autorange_enabled(sensor_mode="power")
        bandwidth_low = self.pm100a.ask("Input:Pdiode:Filter:Lpass:STATe?", data_type="bool")
        averaging = self.pm100a.ask("Sense:Average:COUNt?", data_type="int")

        return PowerMeterSettings(
            wavelength=wavelength * 1e9,
            range=power_range,
            auto_range=auto_range,
            bandwidth="low" if bandwidth_low else "high",
            averaging=averaging,
        )

    def _close_device(self) -> None:
        """PM100Aとの接続を終了する。"""
        if self.pm100a is not None:
            self.pm100a.close()
            self.pm100a = None

    def _apply_settings(self, settings: PowerMeterSettings) -> PowerMeterSettings:
        """波長・レンジ・帯域幅・平均化点数を適用し、実際の設定を返す。

        Args:
            settings (PowerMeterSettings): 波長はnm、レンジはWで指定する。

        Returns:
            PowerMeterSettings: 機器へ適用された設定。要求値が丸められる場合は実際の値。
        """
        if self.pm100a is None or not self.pm100a.is_opened():
            raise ConnectionError("PM100Aに接続されていません")
        if not math.isfinite(settings.wavelength) or settings.wavelength <= 0:
            raise ValueError("波長は正の有限値で指定してください")
        if not settings.auto_range and (not math.isfinite(settings.range) or settings.range <= 0):
            raise ValueError("手動レンジは正の有限値で指定してください")
        if settings.bandwidth not in ("low", "high"):
            raise ValueError("帯域幅はlowまたはhighで指定してください")
        if settings.averaging <= 0:
            raise ValueError("平均化点数は正の整数で指定してください")

        self.pm100a.set_wavelength(settings.wavelength * 1e-9)
        self.pm100a.enable_autorange(settings.auto_range, sensor_mode="power")
        if not settings.auto_range:
            self.pm100a.set_range(settings.range, sensor_mode="power")
        self.pm100a.write(
            "Input:Pdiode:Filter:Lpass:STATe", settings.bandwidth == "low", arg_type="bool"
        )
        self.pm100a.write("Sense:Average:COUNt", settings.averaging, arg_type="int")
        return self._get_settings()

    def _read_sample(self) -> dict[str, object]:
        """現在のパワーモードで新しい測定を行い、W単位の値を返す。

        Returns:
            dict[str, object]: 測定項目名と値。power_wはワット単位のパワー。
        """
        if self.pm100a is None or not self.pm100a.is_opened():
            raise ConnectionError("PM100Aに接続されていません")
        power_w = self.pm100a.get_reading(sensor_mode=None, measure=True, overrng="error")
        return {"power_w": power_w}



def _is_thorlabs_pm100a(resource_name: str) -> bool:
    """VISAリソース名からThorlabs PM100Aかどうかを判定する。

    VIDが0x1313、PIDが0x8071または0x8079のUSBリソースを抽出する。
    例: USB0::0x1313::0x8079::P1005116::INSTR

    Args:
        resource_name (str): VISAリソース名。

    Returns:
        bool: Thorlabs PM100AならTrue、そうでなければFalse。
    """
    parts = resource_name.split("::")
    if len(parts) < 5 or not parts[0].upper().startswith("USB"):
        return False

    try:
        vid = int(parts[1], 16 if parts[1].lower().startswith("0x") else 10)
        pid = int(parts[2], 16 if parts[2].lower().startswith("0x") else 10)
    except ValueError:
        return False

    return vid == 0x1313 and pid in (0x8071, 0x8079)
