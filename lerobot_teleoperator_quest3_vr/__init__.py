"""LeRobot Teleoperator for Quest3 VR-based Piper arm teleoperation.

This package provides a LeRobot-compatible Teleoperator that bridges:
- Quest3 VR controller input (via Vuer)
- End-effector pose mapping (VR → robot frame)
- MINK inverse kinematics (EE target → joint angles)
- VR teleop state machine (RETURNING → AT_ZERO → TELEOP → HOLD)
"""

from .quest3_vr import Quest3VR
from .config_quest3_vr import Quest3VRConfig

__all__ = ["Quest3VR", "Quest3VRConfig"]
