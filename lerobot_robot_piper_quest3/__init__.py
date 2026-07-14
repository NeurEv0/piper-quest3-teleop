"""LeRobot Robot for Piper arm controlled via Quest3 VR teleop.

This package provides a LeRobot-compatible Robot that wraps the Piper hardware
via the LeRobot fork's PIPERFollower, optimized for VR teleop recording.
"""

from .piper_quest3 import PiperQuest3Robot
from .config_piper_quest3 import PiperQuest3Config

__all__ = ["PiperQuest3Robot", "PiperQuest3Config"]
