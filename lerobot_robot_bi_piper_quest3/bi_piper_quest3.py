"""BiPiperQuest3 — LeRobot Robot for two Piper arms in VR teleop context.

Thin subclass of the fork's ``BiPiperFollower`` optimized for dual-arm Quest3 VR
recording. All hardware communication (dual CAN, cameras, drag-teach) is
inherited; this wrapper only supplies VR-friendly defaults and the workspace's
Orbbec camera configuration.

Supports ``mock_hardware=True`` in config for testing without real arms/cameras.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from lerobot.robots.bi_piper_follower import BiPiperFollower
from lerobot.robots.bi_piper_follower.config_bi_piper_follower import BiPiperFollowerConfig
from lerobot.robots.robot import Robot

from .config_bi_piper_quest3 import BiPiperQuest3Config

logger = logging.getLogger(__name__)

# Motor names for a single Piper arm (6 joints + 1 gripper)
_MOTORS = ["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "gripper"]


def _quintic_blend(progress: float) -> float:
    """Zero-velocity/zero-acceleration blend from 0 to 1."""
    t = min(1.0, max(0.0, float(progress)))
    return 10.0 * t**3 - 15.0 * t**4 + 6.0 * t**5


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
            self._disabled_start_pose: dict[str, float] | None = None
        else:
            bi_cfg = _to_bi_piper_config(config)
            self._real = BiPiperFollower(bi_cfg)
            self.cameras = self._real.cameras
            self._is_connected = False
            self._disabled_start_pose: dict[str, float] | None = None
            logger.info(
                "BiPiperQuest3 initialized (left_can=%s, right_can=%s, cameras=%d)",
                bi_cfg.left_can_name,
                bi_cfg.right_can_name,
                len(self.cameras),
            )

    # ── Feature descriptions (used by dataset schema) ──────────────────

    @property
    def observation_features(self) -> dict[str, type | tuple]:
        if not self._mock:
            return self._real.observation_features

        features: dict[str, type] = {}
        for side in ("left_", "right_"):
            for motor in _MOTORS:
                features[f"{side}{motor}.pos"] = float
        return features

    @property
    def action_features(self) -> dict[str, type]:
        if not self._mock:
            return self._real.action_features
        return self.observation_features

    # ── Robot interface ─────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        if not self._mock:
            return self._real.is_connected
        return self._is_connected

    def connect(self, calibrate: bool = True) -> None:
        if self.is_connected:
            return
        if not self._mock:
            self._disabled_start_pose = self._capture_disabled_start_pose()
            if self._disabled_start_pose is None:
                raise RuntimeError(
                    "refusing to enable: a stable pre-enable shutdown pose was not available"
                )
            self._real.connect()
        else:
            self._is_connected = True

    @property
    def is_calibrated(self) -> bool:
        if not self._mock:
            return self._real.is_calibrated
        return True

    def calibrate(self) -> None:
        if not self._mock:
            self._real.calibrate()

    def configure(self) -> None:
        if not self._mock:
            self._real.configure()

    def disconnect(self) -> None:
        if self._mock:
            self._is_connected = False
            return

        arms = (self._real.left_arm, self._real.right_arm)
        if not any(arm.bus.is_connected for arm in arms):
            return

        try:
            self._park_for_disable()
        except Exception:
            logger.exception("Safe shutdown park failed; disabling without a zero-position command")
        finally:
            # Do not call BiPiperFollower.disconnect(): its safe_disconnect()
            # sends a hard-coded all-zero target before disabling.
            for side, arm in zip(("left", "right"), arms):
                if not arm.bus.is_connected:
                    continue
                try:
                    arm.bus.connect(enable=False)
                    logger.info("%s Piper disabled after shutdown park", side)
                except Exception:
                    logger.exception("Failed to disable %s Piper", side)
                arm._is_connected = False
                arm._ema_cmd = None

            for camera in self._real.cameras.values():
                if camera.is_connected:
                    camera.disconnect()

    def _read_unconnected_positions(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for side, arm in (("left", self._real.left_arm), ("right", self._real.right_arm)):
            values = arm.bus.read_action_positions()
            for motor in _MOTORS:
                result[f"{side}_{motor}.pos"] = float(values[f"{motor}.pos"])
        return result

    @staticmethod
    def _pose_is_finite(pose: dict[str, float]) -> bool:
        expected = {f"{side}_{motor}.pos" for side in ("left", "right") for motor in _MOTORS}
        return set(pose) == expected and all(math.isfinite(value) for value in pose.values())

    @staticmethod
    def _pose_is_plausible(pose: dict[str, float]) -> bool:
        joints = [value for key, value in pose.items() if "gripper" not in key]
        grippers = [value for key, value in pose.items() if "gripper" in key]
        return (
            all(abs(value) <= 3.5 for value in joints)
            and all(-0.005 <= value <= 0.10 for value in grippers)
        )

    @staticmethod
    def _pose_delta(a: dict[str, float], b: dict[str, float]) -> float:
        return max(abs(float(a[key]) - float(b[key])) for key in a)

    def _capture_disabled_start_pose(self, timeout_s: float = 2.5) -> dict[str, float] | None:
        """Read a stable encoder pose before enabling either arm."""
        deadline = time.monotonic() + timeout_s
        previous: dict[str, float] | None = None
        while time.monotonic() < deadline:
            try:
                current = self._read_unconnected_positions()
            except Exception:
                time.sleep(0.1)
                continue
            if not self._pose_is_finite(current) or not self._pose_is_plausible(current):
                previous = None
                time.sleep(0.1)
                continue

            joint_values = [
                abs(current[f"{side}_joint_{index}.pos"])
                for side in ("left", "right")
                for index in range(1, 7)
            ]
            # Both arms reporting exact zeros before enable is usually stale CAN
            # feedback on this rig. Never accept it as the shutdown target.
            if max(joint_values, default=0.0) < 0.002:
                previous = None
                time.sleep(0.1)
                continue

            if previous is not None and self._pose_delta(previous, current) <= 0.02:
                logger.info("Captured stable pre-enable pose for normal shutdown: %s", current)
                return current
            previous = current
            time.sleep(0.1)

        logger.error("Could not capture a stable, plausible, non-zero pre-enable pose")
        return None

    def _park_for_disable(self) -> bool:
        """Move both arms smoothly to their captured pre-enable pose."""
        current = self.get_record_action_from_follower()
        if not self._pose_is_finite(current):
            raise RuntimeError("current robot feedback is incomplete or non-finite")
        target = self._disabled_start_pose or dict(current)

        joint_delta = max(
            abs(target[key] - current[key])
            for key in current
            if "gripper" not in key
        )
        gripper_delta = max(
            abs(target[key] - current[key])
            for key in current
            if "gripper" in key
        )
        cfg = self.config
        duration = max(
            float(cfg.shutdown_min_duration_s),
            1.875 * joint_delta / max(float(cfg.shutdown_max_joint_speed_rad_s), 1e-6),
            1.875 * gripper_delta / max(float(cfg.shutdown_max_gripper_speed_m_s), 1e-6),
        )
        if duration > float(cfg.shutdown_max_duration_s):
            raise RuntimeError(f"shutdown target requires {duration:.1f}s, above safety limit")

        rate_hz = max(1.0, float(cfg.shutdown_rate_hz))
        steps = max(2, int(math.ceil(duration * rate_hz)))
        period = 1.0 / rate_hz
        started = time.monotonic()
        logger.info("Parking both Pipers for %.2fs before disable", duration)
        for index in range(1, steps + 1):
            blend = _quintic_blend(index / steps)
            action = {
                key: current[key] + blend * (target[key] - current[key])
                for key in current
            }
            self.send_action(action)
            deadline = started + index * period
            remaining = deadline - time.monotonic()
            if remaining > 0.0:
                time.sleep(remaining)

        settle_deadline = time.monotonic() + max(0.0, float(cfg.shutdown_settle_s))
        while time.monotonic() < settle_deadline:
            self.send_action(target)
            time.sleep(period)

        final = self.get_record_action_from_follower()
        joint_error = max(
            abs(final[key] - target[key]) for key in target if "gripper" not in key
        )
        gripper_error = max(
            abs(final[key] - target[key]) for key in target if "gripper" in key
        )
        reached = (
            joint_error <= float(cfg.shutdown_joint_tolerance_rad)
            and gripper_error <= float(cfg.shutdown_gripper_tolerance_m)
        )
        if reached:
            logger.info(
                "Shutdown pose reached (joint error %.4frad, gripper error %.4fm)",
                joint_error,
                gripper_error,
            )
        else:
            logger.error(
                "Shutdown pose tracking outside tolerance (joint %.4frad, gripper %.4fm)",
                joint_error,
                gripper_error,
            )
        return reached

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

    def get_record_action_from_follower(self) -> dict[str, float]:
        if not self._mock:
            return self._real.get_record_action_from_follower()
        return self.get_observation()
