"""
デバイスをスキャンして、接続可能なデバイスを返す
"""

from backend.app.models.devices import DiscoveredDevice

class DeviceDiscovery:
    def __init__(self, device_classes: list[type]):
        """デバイス探索クラスを初期化する。

        Args:
            device_classes (list[type]): 探索対象のデバイスドライバーのリスト。
        """
        self.device_classes = device_classes

    def scan(self) -> list[DiscoveredDevice]:
        """登録されたすべての種類のデバイスを検索する。

        Returns:
            return (list[DiscoveredDevice]):
                発見されたデバイス一覧。
        """
        discovered = []

        for device_class in self.device_classes:
            discovered.extend(device_class.scan())

        return discovered