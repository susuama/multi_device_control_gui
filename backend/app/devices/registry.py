""" 
デバイスクラスの一覧を登録する
"""

from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.devices.camera.dummy_camera import DummyCamera


DEVICE_CLASSES = [
    DummyPowerMeter,
    DummyCamera,
]
