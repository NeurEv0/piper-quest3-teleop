"""Configuration for Quest3VR teleoperator."""

from dataclasses import dataclass
from typing import Optional

from lerobot.teleoperators.config import TeleoperatorConfig


@TeleoperatorConfig.register_subclass("quest3_vr")
@dataclass(kw_only=True)
class Quest3VRConfig(TeleoperatorConfig):
    """Configuration for Quest3 VR teleoperator.

    Attributes:
        mock_vr: If True, use mock VR input instead of real Quest3 (for testing).
        gripper_alpha: EMA smoothing coefficient for gripper trigger (0-1).
        gripper_max_m: Maximum gripper opening in meters (default 0.07 = 70mm).
        enable_skeleton: If True, render robot skeleton in VR during teleop.
        stream_camera_to_headset: If True (default), stream camera images to the
            Quest3 headset via ImageBackground. Set to False to skip shared-memory
            allocation and JPEG streaming, reducing CPU/memory overhead when the
            headset display is not needed (e.g. during data collection with
            Orbbec cameras).
    """

    mock_vr: bool = False
    gripper_alpha: float = 0.35
    gripper_max_m: float = 0.07
    enable_skeleton: bool = True
    stream_camera_to_headset: bool = True
