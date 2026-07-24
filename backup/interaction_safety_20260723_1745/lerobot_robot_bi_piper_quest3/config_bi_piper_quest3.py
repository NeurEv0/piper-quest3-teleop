"""Configuration for the bi_piper_quest3 robot (dual Piper arms for VR recording).

Thin bridge over the LeRobot fork's ``BiPiperFollowerConfig`` with:
  - VR-optimized defaults (EMA smoothing disabled, drag-teach off),
  - three Orbbec cameras (front + both wrists) whose ``sdk_lib_path`` is resolved
    to the workspace-vendored SDK (keeping this workspace independent of the
    piper_lerobot-main fork tree for the runtime binary).
"""

from dataclasses import dataclass, field

from lerobot.cameras import CameraConfig
from lerobot.cameras.orbbec import OrbbecCameraConfig
from lerobot.robots.config import RobotConfig

# Resolve the Orbbec SDK path from the workspace (env override -> vendored -> fork).
from orbbec_sdk_path import resolve_orbbec_sdk_lib_path

_SDK = resolve_orbbec_sdk_lib_path()


def _default_cameras() -> dict[str, CameraConfig]:
    """Front + left-wrist + right-wrist Orbbec cameras (serials fixed to this rig).

    Keys/resolution/FPS mirror ``bi_piper_follower`` defaults for mixed-training
    compatibility. All three use the Orbbec SDK (stable 3-camera concurrency).
    """
    return {
        "cam_front": OrbbecCameraConfig(
            serial_number="CP0BB530000J", fps=30, width=640, height=480,
            rotation=0, sdk_lib_path=_SDK,
        ),
        "cam_left_wrist": OrbbecCameraConfig(
            serial_number="CC1N16200P0", fps=30, width=640, height=480,
            rotation=0, sdk_lib_path=_SDK,
        ),
        "cam_right_wrist": OrbbecCameraConfig(
            serial_number="CC1N162022N", fps=30, width=640, height=480,
            rotation=0, sdk_lib_path=_SDK,
        ),
    }


@RobotConfig.register_subclass("bi_piper_quest3")
@dataclass(kw_only=True)
class BiPiperQuest3Config(RobotConfig):
    """Config for two Piper arms controlled via Quest3 VR (BiQuest3VR teleop).

    Delegates hardware access to the fork's ``BiPiperFollower``. Key differences
    from the base ``bi_piper_follower``:
      - EMA smoothing disabled (VR IK already produces smooth targets),
      - drag-teach off,
      - Orbbec cameras resolved to the workspace-vendored SDK.
    """

    # CAN interfaces for left and right arms (Linux IFNAMSIZ limit: 15 chars).
    left_can_name: str = "can_left"
    right_can_name: str = "can_right"

    cameras: dict[str, CameraConfig] = field(default_factory=_default_cameras)

    # VR teleop: disable EMA smoothing (IK handles smoothness).
    teleop_joint_alpha: float = 1.0
    teleop_gripper_alpha: float = 1.0

    # When True, skip CAN/Camera hardware init (for testing with mock VR).
    mock_hardware: bool = False

    # Scheme B: record action from follower encoders after send_action.
    record_action_from_follower: bool = True

    # Drag-teach must be off for VR teleop.
    drag_teach_mode: bool = False
    drag_kp: float = 3.0
    drag_kd: float = 0.8
    drag_vel_thresh: float = 0.015
