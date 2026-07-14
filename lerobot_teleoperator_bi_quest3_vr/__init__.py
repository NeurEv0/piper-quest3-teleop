"""LeRobot Teleoperator for dual-arm Quest3 VR teleoperation of two Piper arms.

The left VR controller drives the left arm and the right controller drives the
right arm, each with an independent MINK IK state machine. Emits a 14-DoF action
with ``left_``/``right_`` prefixes matching the ``bi_piper_follower`` robot.
"""

from .bi_quest3_vr import BiQuest3VR
from .config_bi_quest3_vr import BiQuest3VRConfig

__all__ = ["BiQuest3VR", "BiQuest3VRConfig"]
