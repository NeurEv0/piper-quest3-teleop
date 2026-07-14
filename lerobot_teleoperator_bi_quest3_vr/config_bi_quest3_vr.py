"""Configuration for the BiQuest3VR (dual-arm) teleoperator."""

from dataclasses import dataclass

from lerobot.teleoperators.config import TeleoperatorConfig


@TeleoperatorConfig.register_subclass("bi_quest3_vr")
@dataclass(kw_only=True)
class BiQuest3VRConfig(TeleoperatorConfig):
    """Configuration for the dual-arm Quest3 VR teleoperator.

    The left VR controller drives the left Piper arm and the right controller
    drives the right arm. Each arm runs its own independent state machine and
    MINK IK. The action dict uses ``left_``/``right_`` prefixes to match the
    ``bi_piper_follower`` robot.

    Attributes:
        mock_vr: If True, use mock VR input instead of a real Quest3 (testing).
        gripper_alpha: EMA smoothing coefficient for the gripper trigger (0-1).
        gripper_max_m: Maximum gripper opening in meters (default 0.07 = 70mm).
        enable_skeleton: If True, render the robot skeleton in VR during teleop.
            Note: the shared Vuer overlay tracks a single anchor, so with two
            arms the overlay follows the right controller only; set False to
            disable it entirely for bimanual use.
    """

    mock_vr: bool = False
    gripper_alpha: float = 0.35
    gripper_max_m: float = 0.07
    enable_skeleton: bool = False
