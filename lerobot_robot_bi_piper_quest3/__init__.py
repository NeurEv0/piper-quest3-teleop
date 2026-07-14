"""LeRobot Robot for two Piper arms controlled via Quest3 VR teleop.

Wraps the LeRobot fork's ``BiPiperFollower`` with VR-optimized defaults and the
workspace's Orbbec camera configuration (SDK path resolved workspace-locally).
"""

from .bi_piper_quest3 import BiPiperQuest3Robot
from .config_bi_piper_quest3 import BiPiperQuest3Config

__all__ = ["BiPiperQuest3Robot", "BiPiperQuest3Config"]
