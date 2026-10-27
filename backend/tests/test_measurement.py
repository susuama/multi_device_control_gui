# & .\.venv\Scripts\python.exe -m backend.tests.test_measurement

import time

from backend.app.services.device_manager import DeviceManager
from backend.app.services.event_broadcaster import EventBroadcaster
from backend.app.services.measurement_manager import MeasurementManager

from backend.app.devices.registry import DEVICE_CLASSES


def main() -> None:
    """ダミー2台の単発取得・連続取得を手動で確認する。"""
    device_manager = DeviceManager(device_classes=DEVICE_CLASSES)
    measurement_manager = MeasurementManager(device_manager=device_manager, event_broadcaster=EventBroadcaster())

    try:
        device_manager.scan()

        device_ids = list(device_manager.get_all().keys())
        device_id_01 = device_ids[0]
        device_id_02 = device_ids[1]

        # 接続
        device_manager.connect(device_id_01)
        device_manager.connect(device_id_02)

        # 単発取得
        device_manager.read(device_id_01)
        device_manager.read(device_id_02)

        # 連続測定
        measurement_manager.start_sampling(device_id_01)

        time.sleep(1)
        measurement_manager.start_sampling(device_id_02)

        time.sleep(2)

        measurement_manager.stop_sampling(device_id_01)
        measurement_manager.stop_sampling(device_id_02)
    finally:
        # 途中で失敗した場合も、取得・処理スレッドと機器の接続を終了する。
        try:
            measurement_manager.shutdown()
        finally:
            device_manager.shutdown()


# テスト探索時のimportだけでは、測定やスレッドを開始しない。
if __name__ == "__main__":
    main()
