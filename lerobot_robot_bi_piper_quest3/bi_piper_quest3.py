"""BiPiperQuest3Robot — LeRobot Robot for two Piper arms in VR teleop context.

Thin subclass of the fork's ``BiPiperFollower`` optimized for dual-arm Quest3 VR
recording. All hardware communication (dual CAN, cameras, drag-teach) is
inherited; this wrapper only supplies VR-friendly defaults and the workspace's
Orbbec camera configuration.
"""

from __future__ import annotations

import logging

from lerobot.robots.bi_piper_follower import BiPiperFollower
from lerobot.robots.bi_piper_follower.config_bi_piper_follower import BiPiperFollowerConfig

from .config_bi_piper_quest3 import BiPiperQuest3Config

logger = logging.getLogger(__name__)


def _to_bi_piper_config(cfg: BiPiperQuest3Config) -> BiPiperFollowerConfig:
    """Convert BiPiperQuest3Config -> BiPiperFollowerConfig (LeRobot fork)."""
    return BiPiperFollowerConfig(
        id=cfg.id,
        calibration_dir=cfg.calibration_dir,
        left_can_name=cfg.left_can_name,
        right_can_name=cfg.right_can_name,
        cameras=cfg.cameras,
        teleop_joint_alpha=cfg.teleop_joint_alpha,
        teleop_gripper_alpha=cfg.teleop_gripper_alpha,
        record_action_from_follower=cfg.record_action_from_follower,
        drag_teach_mode=cfg.drag_teach_mode,
        drag_kp=cfg.drag_kp,
        drag_kd=cfg.drag_kd,
        drag_vel_thresh=cfg.drag_vel_thresh,
    )


class BiPiperQuest3Robot(BiPiperFollower):
    """Dual Piper arms optimized for Quest3 VR teleop recording.

    Inherits all hardware communication from BiPiperFollower. VR-optimized
    defaults: EMA smoothing disabled, drag-teach off, Orbbec cameras.
    """

    config_class = BiPiperQuest3Config
    name = "bi_piper_quest3"

    def __init__(self, config: BiPiperQuest3Config):
        bi_cfg = _to_bi_piper_config(config)
        super().__init__(bi_cfg)
        logger.info(
            "BiPiperQuest3Robot initialized (left_can=%s, right_can=%s, cameras=%d)",
            bi_cfg.left_can_name,
            bi_cfg.right_can_name,
            len(self.cameras),
        )
