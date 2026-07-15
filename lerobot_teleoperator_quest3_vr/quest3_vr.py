"""Quest3VR Teleoperator — bridges VR teleop into LeRobot's Teleoperator interface.

Single-arm (right controller) Piper teleoperation. The per-frame control logic
(VR pose mapping, MINK IK, teleop state machine, gripper EMA) lives in the
reusable ``teleop.vr_arm_engine.ArmVREngine``; this class handles only the
LeRobot Teleoperator lifecycle and the Vuer/Quest3 I/O.

Data flow per frame:
  1. Recording loop calls robot.get_observation() -> cached via set_observation()
  2. get_action() reads the right VR controller (pose + button state)
  3. ArmVREngine.step() runs the state machine + IK
  4. Returns a joint-space action dict for robot.send_action() and recording.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

from lerobot.teleoperators.teleoperator import Teleoperator
from lerobot.utils.errors import DeviceAlreadyConnectedError, DeviceNotConnectedError

from teleop.vr_arm_engine import ArmVREngine

from .config_quest3_vr import Quest3VRConfig

logger = logging.getLogger(__name__)

# ── VR communication (optional import; mock mode works without it) ─────────
_VUER_AVAILABLE = False
try:
    from teleop.VuerTeleop import VuerTeleop

    _VUER_AVAILABLE = True
except Exception as e:  # pragma: no cover
    logger.warning("VuerTeleop not available: %s", e)


class Quest3VR(Teleoperator):
    """LeRobot Teleoperator for Quest3 VR control of a single Piper robot arm.

    Uses ``set_observation()`` to receive the current robot joint state from the
    recording loop (unused by the pure-VR pipeline today, but kept for interface
    symmetry with the drag-teach teleoperator).
    """

    config_class = Quest3VRConfig
    name = "quest3_vr"

    def __init__(self, config: Quest3VRConfig):
        super().__init__(config)
        self.config = config

        # ── VR communication ──────────────────────────────────────────
        if _VUER_AVAILABLE and not config.mock_vr:
            self._vuer = VuerTeleop(stream_images=config.stream_camera_to_headset)
        else:
            self._vuer = None  # mock mode — no real Quest3

        # ── Per-arm control engine (right controller) ─────────────────
        self._engine = ArmVREngine(
            gripper_alpha=config.gripper_alpha,
            gripper_max_m=config.gripper_max_m,
            enable_skeleton=config.enable_skeleton,
            controller_side="right",
            name="right",
        )

        self._cached_obs: Optional[dict] = None
        self._is_connected: bool = False

        logger.info(
            "Quest3VR initialized (mode=%s, mock_vr=%s)",
            self._engine.mode,
            config.mock_vr,
        )

    # ── Teleoperator interface properties ────────────────────────────────

    @property
    def action_features(self) -> dict[str, type]:
        return {
            "joint_1.pos": float,
            "joint_2.pos": float,
            "joint_3.pos": float,
            "joint_4.pos": float,
            "joint_5.pos": float,
            "joint_6.pos": float,
            "gripper.pos": float,
        }

    @property
    def feedback_features(self) -> dict[str, type]:
        return {}  # No haptic feedback (yet)

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_calibrated(self) -> bool:
        return True  # VR doesn't need calibration

    # ── Connection lifecycle ─────────────────────────────────────────────

    def connect(self) -> None:
        if self._is_connected:
            raise DeviceAlreadyConnectedError("Quest3VR is already connected.")
        self._is_connected = True
        logger.info("Quest3VR connected (Vuer server active in background).")

    def calibrate(self) -> None:
        pass  # VR controller doesn't need calibration

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
        logger.info("Quest3VR disconnected.")

    # ── Observation caching (called by record_loop) ─────────────────────

    def set_observation(self, obs: dict[str, Any]) -> None:
        """Cache robot observation (interface symmetry; unused by pure VR)."""
        self._cached_obs = obs

    # ── Main action computation ──────────────────────────────────────────

    def get_action(self) -> dict[str, Any]:
        """Compute a 7-DoF joint action from the right VR controller + MINK IK."""
        if not self._is_connected:
            raise DeviceNotConnectedError(
                "Quest3VR is not connected. Run connect() first."
            )

        # Read the right VR controller (or mock values).
        if self._vuer is not None and not self.config.mock_vr:
            pose7 = self._vuer.step()  # [x,y,z,qx,qy,qz,qw]
            state14 = self._vuer.right_state
            tv = self._vuer.tv
        else:
            pose7 = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            state14 = np.zeros(14, dtype=float)
            tv = None

        return self._engine.step(pose7, state14, tv=tv)

    def send_feedback(self, feedback: dict[str, Any]) -> None:
        """No force feedback supported yet."""
        pass

    # ── Public helpers ────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        """Current state machine mode (RETURNING / AT_ZERO / TELEOP / HOLD)."""
        return self._engine.mode

    @property
    def last_joint_command(self) -> np.ndarray:
        """Last computed 6-axis joint command (rad)."""
        return self._engine.last_joint_command
