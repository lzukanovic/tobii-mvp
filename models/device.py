"""
Tobii Pro Glasses 3 device status model.
"""
from dataclasses import dataclass, field, asdict


@dataclass
class DeviceStatus:
    """Tracks the current state of the Tobii glasses connection."""
    connected: bool = False
    serial: str = ""
    firmware: str = ""
    battery: float = 0.0
    charging: bool = False
    streaming: bool = False
    calibrated: bool = False
    gaze_freq: int = 0
    gaze_samples: int = 0
    imu_samples: int = 0
    error: str = None

    def to_dict(self):
        return asdict(self)

    def reset(self):
        self.connected = False
        self.serial = ""
        self.firmware = ""
        self.battery = 0.0
        self.charging = False
        self.streaming = False
        self.calibrated = False
        self.gaze_freq = 0
        self.gaze_samples = 0
        self.imu_samples = 0
        self.error = None
