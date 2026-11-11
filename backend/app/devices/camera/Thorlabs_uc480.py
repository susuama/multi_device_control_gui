"""uc480 SDKによる設定変更と連続画像取得。保存ROIの切り出しは保存側が行う。"""

import os
import time
import datetime
import threading
from queue import Full, Queue
from typing import Any

import numpy as np
import cv2
from PIL import Image

from pylablib.devices.uc480.uc480 import list_cameras, find_by_serial
from pylablib.devices.uc480.uc480 import UC480Camera, uc480Error

from backend.app.models.devices import CameraSetting, DeviceInfo, DiscoveredDevice
from backend.app.models.measurement import CameraFrame, DeviceData
from backend.app.utils.logging_utils import setup_logger
from backend.app.utils.time_utils import get_counter_ns


class UC480:
    """uc480カメラの設定通知と8bit画像を共通Queueへ渡す。"""

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

        self.uc480: UC480Camera | None = None  # SDKのハンドル。実機用クラスでは初期化時に追加する。
        self._device_info = None

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
                try:
                    self.stop_sampling()
                except Exception:
                    thread = self._sampling_thread
                    if thread is not None and thread.is_alive():
                        raise
                    # スレッド終了後の撮影停止失敗でも、ハンドルの解放を試みる。
                    self.logger.exception("Could not stop UC480 acquisition before disconnect")
                with self._io_lock:
                    self._close_device()
                    self.connected = False
                    self.last_error = None
                return True
            except Exception as exc:
                reason = str(exc) or type(exc).__name__
                self.last_error = f"{self.last_error}; 切断失敗: {reason}" if self.last_error else reason
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
                    message = "Timed out waiting for sampling to stop."
                    self.last_error = self.last_error or message
                    raise TimeoutError(message)
                self._sampling_thread = None
            with self._io_lock:
                if self.uc480 is not None:
                    self.uc480.stop_acquisition()
                self.data_queue = None

    def is_sampling(self) -> bool:
        """画像取得スレッドが動作中かどうかを返す。"""
        with self._state_lock:
            return self._sampling_thread is not None and self._sampling_thread.is_alive()

    def _sample_data(self) -> None:
        """カメラの連続撮影から順にフレームを読み出し、Queueへ追加する。"""
        try:
            while not self._stop_event.is_set():
                with self._io_lock:
                    if self._stop_event.is_set():
                        break
                    self._put_data(self.read())
                # ロックを解放して短く待ち、設定操作側が取得する機会を作る。
                self._stop_event.wait(0.001)
        except Full as exc:
            self.logger.error("Sampling stopped for %s: %s", self.device_id, exc)
        except Exception as exc:
            if not self._stop_event.is_set():
                self.connected = False
                self.last_error = self.last_error or str(exc) or type(exc).__name__
                self.logger.exception("Sampling failed for %s", self.device_id)
                report_error = getattr(self.data_queue, "report_error", None)
                if report_error is not None:
                    report_error(self.device_id, self.last_error)
        finally:
            self._stop_event.set()
            with self._io_lock:
                if self.uc480 is not None:
                    try:
                        self.uc480.stop_acquisition()
                    except Exception as exc:
                        self.last_error = self.last_error or str(exc) or type(exc).__name__
                        self.logger.exception("Could not stop UC480 acquisition")

    # ========================== デバイス固有処理 ==========================
    # 実機用のクラスでは、以下をSDKの探索・接続・設定・取得処理へ置き換える。

    @classmethod
    def scan(cls) -> list[DiscoveredDevice]:
        """uc480 SDKで検出したカメラの識別情報とデバイスIDを返す。"""
        for camera in list_cameras(backend="uc480"):
            print(camera.model, camera.serial_number, camera.dev_id)

        return [
            DiscoveredDevice(
                driver=cls.__name__,
                type="camera",
                manufacturer="Thorlabs",
                model=camera.model,
                serial_number=camera.serial_number,
                address=str(camera.dev_id),
            )
            for camera in list_cameras(backend="uc480")
        ]

    def _open_device(self) -> CameraSetting:
        """シリアル番号で接続先を確認し、8bit画像形式の撮影設定を返す。"""
        serial = self.serial_number
        if serial == "":
            raise ValueError("UC480のシリアル番号が指定されませんでした。")
        
        dev_id = find_by_serial(serial, backend="uc480")
        uc480 = UC480Camera(dev_id=dev_id, backend="uc480")
        self.uc480 = uc480

        try:
            if not uc480.is_opened():
                raise ConnectionError(f"UC480に接続できませんでした: {self.address}")
            
            info = uc480.get_device_info()
            if info.serial_number != serial:
                raise ValueError(
                    f"UC480のシリアル番号が一致しません: 要求={serial}, 接続先={info.serial_number}"
                )
            
            if uc480.get_color_mode() not in ("mono8", "rgb8p", "bgr8p"):
                uc480.set_color_mode("mono8")
            settings = self._get_settings()

        except Exception:
            try:
                self._close_device()
            except Exception as close_error:
                self.last_error = f"UC480の接続失敗後に機器を閉じられませんでした: {close_error}"
                self.logger.exception("Could not close UC480 after connection failed: %s", serial)
            raise

        self._device_info = info
        self.logger.debug(f"UC480 {serial} is successfully connected.")
        
        return settings

    def is_connected(self) -> bool:
        """SDKの機器一覧と情報取得で、カメラの接続を確認する。"""
        with self._io_lock:
            if self.uc480 is None or not self.uc480.is_opened():
                return False
            # 抜線後にハンドルが残る場合も、SDKの現在の機器一覧と照合する。
            if not any(camera.serial_number == self.serial_number
                       for camera in list_cameras(backend="uc480")):
                return False
            info = self.uc480.get_device_info()
            return info.serial_number == self.serial_number

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
            type="camera",
            manufacturer=info.manufacturer if info else "Thorlabs",
            model=info.model if info else "Unknown",
            serial_number=info.serial_number if info else self.serial_number,
            address=self.address,
            status="error" if self.last_error else "connected" if self.connected else "disconnected",
            error=self.last_error,
        )

    def _get_settings(self) -> CameraSetting:
        """カメラの現在の設定を取得する。"""
        cam = self.uc480

        height, width = cam.get_data_dimensions()
        pixel_format_map = {
            "mono8": "mono8",
            "rgb8p": "rgb24",
            "bgr8p": "bgr24",
        }

        return CameraSetting(
            width=width,
            height=height,
            fps=1.0 / cam.get_frame_period(),
            exposure=cam.get_exposure(),
            gain=cam.get_gains()[0],
            gain_boost=cam.get_gain_boost(),
            pixel_format=pixel_format_map[cam.get_color_mode()],
            roi=None,
        )

    def _close_device(self) -> None:
        """uc480との接続を終了する。"""
        if self.uc480 is not None:
            try:
                self.uc480.close()
            except uc480Error:
                # SDKの撮影停止が失敗しても、カメラハンドルの終了を試みる。
                self.logger.exception("Could not close UC480 through acquisition cleanup")
                self.uc480.lib.is_ExitCamera(self.uc480.hcam)
                self.uc480.hcam = None
            self.uc480 = None

    def _apply_settings(self, settings: CameraSetting) -> CameraSetting:
        """撮影を停止して設定を適用し、読み戻し後に元の取得状態へ戻す。

        Args:
            settings (CameraSetting): 撮影条件とソフトウェア保存用ROI。画像サイズは変更しない。

        Returns:
            CameraSetting: SDKから読み戻した撮影条件と、指定された保存ROI。
        """
        if self.uc480 is None or not self.uc480.is_opened():
            raise ConnectionError("UC480に接続されていません")
        
        cam = self.uc480
        height, width = cam.get_data_dimensions()
        if (settings.width, settings.height) != (width, height):
            raise ValueError("取得画像サイズは変更できません。切り出しには保存ROIを指定してください")
        
        was_running = cam.acquisition_in_progress()
        try:
            cam.clear_acquisition()
            cam.set_color_mode(
                {
                    "mono8": "mono8", 
                    "rgb24": "rgb8p", 
                    "bgr24": "bgr8p"
                }[settings.pixel_format]
            )
            cam.set_frame_period(1.0 / settings.fps)
            cam.set_exposure(settings.exposure)
            cam.set_gains(master=settings.gain)
            cam.set_gain_boost(settings.gain_boost)
            applied = self._get_settings()
            applied = CameraSetting.model_validate({**applied.model_dump(), "roi": settings.roi})
            if was_running:
                cam.start_acquisition()
            return applied
        
        except Exception as exc:
            # 一部だけ適用された設定で取得を継続しない。
            self.last_error = str(exc) or type(exc).__name__
            self._stop_event.set()
            raise

    def _read_frame(self) -> CameraFrame:
        """古い未読画像を取得し、再利用されない8bit画像と機器時刻を返す。

        停止要求は最大0.1秒ごとのフレーム待機の間で確認する。

        Returns:
            CameraFrame: カメラのフレーム番号、ns単位の機器時刻、画像を保持する。
        """
        if self.uc480 is None or not self.uc480.is_opened():
            raise ConnectionError("UC480に接続されていません")
        
        cam = self.uc480
        if self._stop_event.is_set():
            raise InterruptedError("UC480の取得停止が要求されました")
        
        if not cam.acquisition_in_progress():
            cam.start_acquisition()

        # 露光時間 or 撮影周期 の二倍の時間待機しても新規画像が取得できない場合はエラーを出す。
        deadline = time.monotonic() + max(2.0, 2 * self.settings.exposure, 2 / self.settings.fps)
        while not self._stop_event.is_set():
            try:
                cam.wait_for_frame(timeout=0.1, error_on_stopped=True)
            except cam.TimeoutError:
                if time.monotonic() >= deadline:
                    raise
                continue

            result = cam.read_oldest_image(return_info=True)
            if result is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("UC480から画像を取得できませんでした")
                continue

            image, info = result
            expected_shape = (self.settings.height, self.settings.width)
            if self.settings.pixel_format != "mono8":
                expected_shape += (3,)
            if image.dtype != np.uint8 or image.shape != expected_shape:
                raise ValueError("UC480の画像形式またはサイズが設定と一致しません")
            
            # SDKや呼び出し元の配列が更新されても、Queue内の画像は変化しない。
            owned_image = memoryview(image.tobytes(order="C")).cast("B", shape=expected_shape)
            return CameraFrame(
                image=owned_image,
                frame_index=int(info.framestamp),
                device_timestamp_ns=int(info.timestamp_dev) * 100,
            )
        raise InterruptedError("UC480の取得停止が要求されました")
