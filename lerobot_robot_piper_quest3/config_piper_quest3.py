"""Configuration for PiperQuest3 robot (Piper arm for VR teleop recording)."""

from dataclasses import dataclass, field

from lerobot.robots.config import RobotConfig
from lerobot.cameras.configs import CameraConfig
from lerobot.cameras.orbbec import OrbbecCameraConfig

# Resolve the Orbbec SDK path from the workspace (env override -> vendored -> fork),
# keeping this workspace independent of the piper_lerobot-main fork tree.
from orbbec_sdk_path import resolve_orbbec_sdk_lib_path

_SDK = resolve_orbbec_sdk_lib_path()


def _default_cameras() -> dict[str, CameraConfig]:
    """Front + right-wrist Orbbec cameras for the single (right) Piper arm.

    Uses the Orbbec SDK (stable multi-camera concurrency on this rig), not OpenCV.
    Override via ``--robot.cameras='{...}'`` on the CLI as needed.
    """
    return {
        "cam_front": OrbbecCameraConfig(
            serial_number="CP0BB530000J", fps=30, width=640, height=480,
            rotation=0, sdk_lib_path=_SDK,
        ),
        "cam_right_wrist": OrbbecCameraConfig(
            serial_number="CC1N162022N", fps=30, width=640, height=480,
            rotation=0, sdk_lib_path=_SDK,
        ),
    }


@RobotConfig.register_subclass("piper_quest3")
@dataclass(kw_only=True)
class PiperQuest3Config(RobotConfig):
    """Configuration for a Piper arm controlled via Quest3 VR teleop.

    This is a bridge config that delegates to the LeRobot fork's PIPERFollower
    for hardware access. Key differences from standard PIPERFollower:
    - EMA smoothing disabled by default (VR teleop handles smoothness via IK)
    - Camera config exposed at this level (Orbbec SDK, workspace-vendored)
    - ``record_action_from_follower`` enabled by default (Scheme B)
    """

    # CAN interface name. Single-arm VR uses the right arm -> "can_right".
    can_name: str = "can_right"

    # Camera configurations. Defaults to Orbbec front + right-wrist (Orbbec SDK).
    cameras: dict[str, CameraConfig] = field(default_factory=_default_cameras)

    # Joint command EMA (0-1, 1=disabled). VR teleop uses IK for smoothness.
    teleop_joint_alpha: float = 1.0

    # Gripper command EMA (0-1). Handled by VR teleoperator internally.
    teleop_gripper_alpha: float = 1.0

    # Scheme B: record action from follower encoders after send_action
    record_action_from_follower: bool = True

    # Enable MIT drag-teach mode (should be False for VR teleop)
    drag_teach_mode: bool = False

    # MIT drag-teach gains (unused when drag_teach_mode=False)
    drag_kp: float = 3.0
    drag_kd: float = 0.8
    drag_vel_thresh: float = 0.01
