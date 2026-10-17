""" 
デバイスクラスの一覧を登録する
"""

from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter


DEVICE_CLASSES = [
    DummyPowerMeter,
]