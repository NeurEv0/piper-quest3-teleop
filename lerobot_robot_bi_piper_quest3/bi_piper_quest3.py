"""BiPiperQuest3 — LeRobot Robot for two Piper arms in VR teleop context.

Thin subclass of the fork's ``BiPiperFollower`` optimized for dual-arm Quest3 VR
recording. All hardware communication (dual CAN, cameras, drag-teach) is
inherited; this wrapper only supplies VR-friendly defaults and the workspace's
Orbbec camera configuration.

Supports ``mock_hardware=True`` in config for testing without real arms/cameras.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from lerobot.robots.bi_piper_follower import BiPiperFollower
from lerobot.robots.bi_piper_follower.config_bi_piper_follower import BiPiperFollowerConfig
from lerobot.robots.robot import Robot

from .config_bi_piper_quest3 import BiPiperQuest3Config

logger = logging.getLogger(__name__)

# Motor names for a single Piper arm (6 joints + 1 gripper)
_MOTORS = ["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "gripper"]


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


class BiPiperQuest3(Robot):
    """Dual Piper arms optimized for Quest3 VR teleop recording.

    When ``mock_hardware=True``, skips CAN/camera initialization and provides
    synthetic zero observations — usable for end-to-end testing of the
    VR teleoperation data collection pipeline without physical arms or cameras.
    """

    config_class = BiPiperQuest3Config
    name = "bi_piper_quest3"

    def __init__(self, config: BiPiperQuest3Config):
        super().__init__(config)
        self.config = config
        self._mock = bool(config.mock_hardware)

        if self._mock:
            logger.info("BiPiperQuest3: mock_hardware=True — skipping CAN/cameras")
            self.cameras = {}
            self._is_connected = False
        else:
            bi_cfg = _to_bi_piper_config(config)
            self._real = BiPiperFollower(bi_cfg)
            self.cameras = self._real.cameras
            self._is_connected = False
            logger.info(
                "BiPiperQuest3 initialized (left_can=%s, right_can=%s, cameras=%d)",
                bi_cfg.left_can_name,
                bi_cfg.right_can_name,
                len(self.cameras),
            )

    # ── Feature descriptions (used by dataset schema) ──────────────────

    @property
    def observation_features(self) -> dict[str, type]:
        features: dict[str, type] = {}
        for side in ("left_", "right_"):
            for motor in _MOTORS:
                features[f"{side}{motor}.pos"] = float
        return features

    @property
    def action_features(self) -> dict[str, type]:
        return self.observation_features

    # ── Robot interface ─────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def connect(self, calibrate: bool = True) -> None:
        if self._is_connected:
            return
        if not self._mock:
            self._real.connect()
        self._is_connected = True

    @property
    def is_calibrated(self) -> bool:
        return True

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def disconnect(self) -> None:
        if not self._is_connected:
            return
        if not self._mock:
            self._real.disconnect()
        self._is_connected = False

    def get_observation(self) -> dict[str, Any]:
        if self._mock:
            obs: dict[str, Any] = {}
            for side in ("left_", "right_"):
                for motor in _MOTORS:
                    obs[f"{side}{motor}.pos"] = 0.0
            return obs
        return self._real.get_observation()

    def send_action(self, action: dict[str, Any]) -> dict[str, Any]:
        if not self._mock:
            return self._real.send_action(action)
        return action
