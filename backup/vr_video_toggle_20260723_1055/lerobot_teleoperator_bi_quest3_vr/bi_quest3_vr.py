"""BiQuest3VR Teleoperator — dual-arm Quest3 VR control for two Piper arms.

Composes two independent ``ArmVREngine`` instances:
  - the LEFT VR controller drives the LEFT arm,
  - the RIGHT VR controller drives the RIGHT arm.

Each engine runs its own RETURNING -> AT_ZERO -> TELEOP -> HOLD state machine and
MINK IK. The emitted 14-DoF action dict uses ``left_``/``right_`` prefixes so it
pairs directly with the ``bi_piper_follower`` robot.

Data flow per frame:
  1. Read both VR controllers via VuerTeleop.step_both() + left/right_state.
  2. Left engine.step(left_pose, left_state) -> left_* action.
  3. Right engine.step(right_pose, right_state) -> right_* action.
  4. Return the merged 14-DoF action dict for robot.send_action() + recording.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import numpy as np

from lerobot.teleoperators.teleoperator import Teleoperator
from lerobot.utils.errors import DeviceAlreadyConnectedError, DeviceNotConnectedError

from teleop.vr_arm_engine import ArmVREngine

from .config_bi_quest3_vr import BiQuest3VRConfig

logger = logging.getLogger(__name__)

# ── VR communication (optional import; mock mode works without it) ─────────
_VUER_AVAILABLE = False
try:
    from teleop.VuerTeleop import VuerTeleop

    _VUER_AVAILABLE = True
except Exception as e:  # pragma: no cover
    logger.warning("VuerTeleop not available: %s", e)

_MOTORS = ["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "gripper"]


class BiQuest3VR(Teleoperator):
    """LeRobot Teleoperator for dual-arm Quest3 VR control of two Piper arms."""

    config_class = BiQuest3VRConfig
    name = "bi_quest3_vr"

    def __init__(self, config: BiQuest3VRConfig):
        super().__init__(config)
        self.config = config

        # ── VR communication (shared server, both controllers) ─────────
        if _VUER_AVAILABLE and not config.mock_vr:
            self._vuer = VuerTeleop(stream_images=config.stream_camera_to_headset)
        else:
            self._vuer = None  # mock mode — no real Quest3

        # ── Two per-arm control engines ───────────────────────────────
        engine_kwargs = dict(
            gripper_alpha=config.gripper_alpha,
            gripper_max_m=config.gripper_max_m,
            enable_skeleton=config.enable_skeleton,
        )
        self._left_engine = ArmVREngine(controller_side="left", name="left", **engine_kwargs)
        self._right_engine = ArmVREngine(controller_side="right", name="right", **engine_kwargs)

        self._cached_obs: Optional[dict] = None
        self._is_connected: bool = False
        self._last_vr_sample: dict[str, Any] | None = None

        logger.info(
            "BiQuest3VR initialized (left=%s, right=%s, mock_vr=%s)",
            self._left_engine.mode,
            self._right_engine.mode,
            config.mock_vr,
        )

    # ── Teleoperator interface properties ────────────────────────────────

    @property
    def action_features(self) -> dict[str, type]:
        features: dict[str, type] = {}
        for side in ("left_", "right_"):
            for motor in _MOTORS:
                features[f"{side}{motor}.pos"] = float
        return features

    @property
    def feedback_features(self) -> dict[str, type]:
        return {}

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_calibrated(self) -> bool:
        return True

    # ── Connection lifecycle ─────────────────────────────────────────────

    def connect(self) -> None:
        if self._is_connected:
            raise DeviceAlreadyConnectedError("BiQuest3VR is already connected.")
        self._is_connected = True
        logger.info("BiQuest3VR connected (Vuer server active in background).")

    def calibrate(self) -> None:
        pass

    def configure(self) -> None:
        pass

    def disconnect(self) -> None:
        if not self._is_connected:
            return
        if self._vuer is not None:
            try:
                self._vuer.close()
            except Exception:
                pass
        self._is_connected = False
        logger.info("BiQuest3VR disconnected.")

    # ── Observation caching (called by record_loop) ─────────────────────

    def set_observation(self, obs: dict[str, Any]) -> None:
        """Cache robot observation (interface symmetry; unused by pure VR)."""
        self._cached_obs = obs

    # ── Main action computation ──────────────────────────────────────────

    def get_action(self) -> dict[str, Any]:
        """Compute a 14-DoF action from both VR controllers + MINK IK."""
        if not self._is_connected:
            raise DeviceNotConnectedError(
                "BiQuest3VR is not connected. Run connect() first."
            )

        # Read both VR controllers (or mock values).
        if self._vuer is not None and not self.config.mock_vr:
            left_pose, right_pose = self._vuer.step_both()
            left_state = self._vuer.left_state
            right_state = self._vuer.right_state
            tv = self._vuer.tv
        else:
            left_pose = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            right_pose = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            left_state = np.zeros(14, dtype=float)
            right_state = np.zeros(14, dtype=float)
            tv = None

        sample_ns = time.monotonic_ns()
        self._last_vr_sample = {
            "host_monotonic_ns": sample_ns,
            "left_pose_xyzw": np.asarray(left_pose, dtype=float).tolist(),
            "right_pose_xyzw": np.asarray(right_pose, dtype=float).tolist(),
            "left_state": np.asarray(left_state, dtype=float).tolist(),
            "right_state": np.asarray(right_state, dtype=float).tolist(),
            "event_status": (
                self._vuer.controller_event_status
                if self._vuer is not None and not self.config.mock_vr
                else {"event_count": 0, "last_event_ns": sample_ns, "age_s": 0.0}
            ),
        }

        left_action = self._left_engine.step(left_pose, left_state, tv=tv)
        right_action = self._right_engine.step(right_pose, right_state, tv=tv)

        action: dict[str, Any] = {}
        for key, value in left_action.items():
            action[f"left_{key}"] = value
        for key, value in right_action.items():
            action[f"right_{key}"] = value
        return action

    def send_feedback(self, feedback: dict[str, Any]) -> None:
        pass

    # ── Public helpers ────────────────────────────────────────────────────

    @property
    def mode(self) -> tuple[str, str]:
        """Current (left, right) state machine modes."""
        return self._left_engine.mode, self._right_engine.mode

    @property
    def last_vr_sample(self) -> dict[str, Any] | None:
        """Latest raw Quest poses/states with host freshness information."""
        return None if self._last_vr_sample is None else dict(self._last_vr_sample)
