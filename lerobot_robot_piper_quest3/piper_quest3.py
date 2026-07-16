"""PiperQuest3 — LeRobot Robot for Piper arm in VR teleop context.

Thin subclass of PIPERFollower optimized for VR teleop recording.
"""

from __future__ import annotations

import logging
from typing import Any

from lerobot.robots.piper_follower import PIPERFollower
from lerobot.robots.piper_follower.config_piper_follower import PIPERFollowerConfig

from .config_piper_quest3 import PiperQuest3Config

logger = logging.getLogger(__name__)


def _to_piper_config(cfg: PiperQuest3Config) -> PIPERFollowerConfig:
    """Convert PiperQuest3Config → PIPERFollowerConfig (LeRobot fork)."""
    return PIPERFollowerConfig(
        id=cfg.id,
        calibration_dir=cfg.calibration_dir,
        can_name=cfg.can_name,
        cameras=cfg.cameras,
        teleop_joint_alpha=cfg.teleop_joint_alpha,
        teleop_gripper_alpha=cfg.teleop_gripper_alpha,
        record_action_from_follower=cfg.record_action_from_follower,
        drag_teach_mode=cfg.drag_teach_mode,
        drag_kp=cfg.drag_kp,
        drag_kd=cfg.drag_kd,
        drag_vel_thresh=cfg.drag_vel_thresh,
    )


class PiperQuest3(PIPERFollower):
    """Piper robot arm optimized for Quest3 VR teleop recording.

    Inherits all hardware communication from PIPERFollower.
    VR-optimized defaults: EMA smoothing disabled, Scheme B recording.
    """

    config_class = PiperQuest3Config
    name = "piper_quest3"

    def __init__(self, config: PiperQuest3Config):
        piper_cfg = _to_piper_config(config)
        super().__init__(piper_cfg)
        logger.info(
            "PiperQuest3 initialized (can=%s, cameras=%d)",
            piper_cfg.can_name,
            len(self.cameras),
        )
