"""
複数のデバイスを管理する。
"""

import threading

from backend.app.models.devices import DiscoveredDevice
from backend.app.utils.logging_utils import setup_logger


class DeviceManager:
    """
    複数のデバイスを管理する。
    """

    def __init__(self, device_classes: list[type]):
        self.devices = {}   # キーはデバイスID、値はデバイスオブジェクト
        self.device_classes = device_classes

        self._lock = threading.RLock()      # デバイスの登録・取得を同期する
        self._scan_lock = threading.Lock()  # 複数のscan()が同時に実行されることを防ぐ

        # ロガーの初期化
        self.logger = setup_logger(__class__.__name__)

    def scan(self) -> None:
        """
        登録されたすべての種類のデバイスを検索する。
        """
        with self._scan_lock:
            discovered = []

            for device_class in self.device_classes:
                discovered.extend(device_class.scan())

            self._update_devices(discovered)

    def _update_devices(self, discovered_devices: list[DiscoveredDevice]) -> None:
        """
        発見されたデバイス情報をもとに、デバイスの登録を更新する。

        Args:
            discovered_devices (list[DiscoveredDevice]): 発見されたデバイスのリスト。
        """
        discovered_ids = set()

        for discovered in discovered_devices:
            device_id = self._create_device_id(discovered)
            discovered_ids.add(device_id)

            # 未登録かどうかを確認
            with self._lock:
                device = self.devices.get(device_id)

            if device is None:
                # デバイスが未登録の場合、新規にインスタンス化して登録する
                device_class = next(
                    (cls for cls in self.device_classes if cls.__name__ == discovered.driver),
                    None,
                )

                if device_class is None:
                    self.logger.warning("Device driver not found: %s", discovered.driver)
                    continue

                device_instance = device_class(
                    device_id=device_id,
                    serial_number=discovered.serial_number,
                    address=discovered.address,
                )

                self.register(device_id, device_instance)

            else:
                # 使用中の通信先を変えないよう、停止・切断済みの機器だけ更新する。
                if device.get_info().status == "disconnected" and not device.is_sampling():
                    device.update_address(discovered.address)

        # 今回のscanで検出されなかったデバイスを切断する
        errors = []

        # 未検出のデバイスを切断する
        for device_id, device in self.get_all().items():
            if device_id not in discovered_ids:
                try:
                    success = device.disconnect()

                    if not success:
                        raise RuntimeError("Device disconnect returned False.")

                except Exception as exc:
                    errors.append(f"{device_id}: {exc}")
                    self.logger.exception("Could not disconnect missing device %s", device_id)

        # 未検出でもインスタンスは保持し、後続の停止・状態取得を可能にする
        if errors:
            raise RuntimeError("Could not stop missing devices: " + "; ".join(errors))

    def _create_device_id(self, info: DiscoveredDevice) -> str:
        """
        デバイス情報から一意のデバイスIDを生成する。

        Args:
            info (DiscoveredDevice): 発見されたデバイス情報。

        Returns:
            return (str): デバイスID。
        """
        identifier = info.serial_number or info.address

        if not identifier:
            raise ValueError(f"Device has no serial number or address: {info.driver}")

        return f"{info.driver}_{identifier}"

    # ========================== Shutdown ==========================

    def shutdown(self) -> None:
        """
        登録されている全てのデバイスを停止・切断する。
        """
        self.disconnect_all()
        self.logger.info("All devices have been disconnected.")

    # ...............................................................

    def register(self, device_id: str, device) -> None:
        """
        デバイスを登録する。

        Args:
            device_id (str): デバイスのID。
            device: デバイスオブジェクト。
        """
        with self._lock:
            self.devices[device_id] = device

    def unregister(self, device_id: str) -> None:
        """
        デバイスを停止・切断して登録解除する。

        Args:
            device_id (str): デバイスのID。
        """
        device = self.get(device_id)

        # 実機操作中はDeviceManagerのLockを保持しない
        success = device.disconnect()

        if not success:
            raise RuntimeError(f"Could not disconnect device: {device_id}")

        with self._lock:
            # disconnect中に別インスタンスへ置き換わっていないことを確認する
            if self.devices.get(device_id) is device:
                self.devices.pop(device_id)

    def get(self, device_id: str):
        """
        デバイスを取得する。

        Args:
            device_id (str): デバイスのID。

        Returns:
            return: デバイスオブジェクト。
        """
        with self._lock:
            device = self.devices.get(device_id)

        if device is None:
            raise KeyError(f"Device not found: {device_id}")

        return device

    def get_all(self):
        """
        登録されている全てのデバイスを取得する。

        Returns:
            return (dict): デバイスIDとデバイスオブジェクトの辞書。
        """
        with self._lock:
            # 呼び出し元が一覧を走査している間の登録変更から切り離す
            return self.devices.copy()

    # ...............................................................

    def connect(self, device_id: str) -> bool:
        """
        指定されたデバイスに接続する。

        Args:
            device_id (str): デバイスのID。

        Returns:
            return (bool): 接続成功ならTrue、失敗ならFalse。
        """
        device = self.get(device_id)
        return device.connect()

    def disconnect(self, device_id: str) -> bool:
        """
        指定されたデバイスとの接続を切断する。

        Args:
            device_id (str): デバイスのID。

        Returns:
            return (bool): 切断成功ならTrue、失敗ならFalse。
        """
        device = self.get(device_id)
        return device.disconnect()

    def disconnect_all(self) -> None:
        """
        登録されている全てのデバイスとの接続を切断する。
        """
        # 1台の失敗で後続の切断を中断せず、最後にまとめてエラーにする
        errors = []

        for device_id, device in self.get_all().items():
            try:
                success = device.disconnect()

                if not success:
                    raise RuntimeError("Device disconnect returned False.")

            except Exception as exc:
                errors.append(f"{device_id}: {exc}")
                self.logger.exception("Could not disconnect device %s", device_id)

        if errors:
            raise RuntimeError("Could not disconnect all devices: " + "; ".join(errors))

    # ...............................................................

    def get_info(self, device_id: str):
        """
        指定されたデバイスの情報を取得する。
        """
        device = self.get(device_id)
        return device.get_info()

    def get_settings(self, device_id: str):
        """
        指定されたデバイスの設定を取得する。
        """
        device = self.get(device_id)
        return device.get_settings()

    def set_settings(self, device_id: str, settings):
        """
        指定されたデバイスの設定を更新する。
        """
        device = self.get(device_id)
        return device.set_settings(settings)

    def read(self, device_id: str):
        """
        指定されたデバイスからデータを取得する。

        Args:
            device_id (str): デバイスのID。

        Returns:
            return: デバイスから取得したデータ。
        """
        device = self.get(device_id)
        data = device.read()

        self.logger.debug(f"Read data from device {device_id}: {data}")

        return data
