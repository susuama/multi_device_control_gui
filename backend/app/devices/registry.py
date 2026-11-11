""" 
デバイスクラスの一覧を登録する
"""

from backend.app.devices.power_meter.dummy_power_meter import DummyPowerMeter
from backend.app.devices.power_meter.Thorlabs_PM100A import PM100A
from backend.app.devices.camera.dummy_camera import DummyCamera
from backend.app.devices.camera.Thorlabs_uc480 import UC480

DEVICE_CLASSES = [
    DummyPowerMeter,
    DummyCamera,
    PM100A,
    UC480
]
