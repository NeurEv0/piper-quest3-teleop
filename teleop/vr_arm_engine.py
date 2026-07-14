"""Reusable single-arm VR control engine for Piper + Quest3 teleop.

This module extracts the per-arm control pipeline that used to live inline in
the ``Quest3VR`` LeRobot teleoperator so it can be instantiated once per arm and
reused for bimanual (dual-arm) teleoperation.

Pipeline per ``step()``:
  1. Parse controller button state (squeeze / return-to-zero / trigger).
  2. Update gripper EMA from the trigger value.
  3. Run the teleop state machine: RETURNING -> AT_ZERO -> TELEOP -> HOLD.
  4. Map the VR controller pose to an EE target and solve MINK IK.
  5. Return a flat 7-DoF joint action dict.

The engine is deliberately free of any Vuer/network I/O: the caller passes in
the already-read controller ``pose7`` and ``state14`` each frame. An optional
``tv`` handle is used only for the in-headset skeleton overlay.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Optional heavy deps (mujoco / mink / teleop package) ──────────────────
_ENGINE_DEPS_AVAILABLE = False
try:
    import mujoco
    import mink
    from loop_rate_limiters import RateLimiter

    from .config import PIPER_MJCF_PATH, MINK_EE_SITE
    from .mapping.vr_mapper import VRToRobotMapper, VRMapperConfig
    from .control.ik_stepper import ik_step
    from .kinematics.piper_forward_kinematics import PiperForwardKinematics, DHType
    from .utils.joint123_near_zero import joints123_near_zero

    _ENGINE_DEPS_AVAILABLE = True
except Exception as e:  # pragma: no cover - exercised only when deps missing
    logger.warning("VR arm engine deps not fully available: %s", e)


# ── Controller state14 layout (see teleop/TeleVision.py on_controller_move) ──
_IDX_SQUEEZE = 1       # squeeze pressed (bool as float): engage/disengage teleop
_IDX_RETURN = 4        # aButton pressed: return-to-zero
_IDX_TRIGGER_VALUE = 6  # analog trigger 0.0-1.0: gripper


class ArmVREngine:
    """One Piper arm's worth of VR control state + IK.

    Args:
        gripper_alpha: EMA smoothing for the trigger->gripper mapping (0-1).
        gripper_max_m: Gripper opening in meters at trigger=0 (fully open).
        enable_skeleton: Render the robot skeleton overlay in the headset on
            teleop entry (requires a ``tv`` handle at ``step()`` time).
        controller_side: "left" or "right" — which ``tv.*_controller`` anchor
            to use for the skeleton overlay.
        name: Short label for logging (e.g. "left" / "right").
    """

    def __init__(
        self,
        *,
        gripper_alpha: float = 0.35,
        gripper_max_m: float = 0.07,
        enable_skeleton: bool = True,
        controller_side: str = "right",
        name: str = "arm",
    ) -> None:
        if controller_side not in ("left", "right"):
            raise ValueError(f"controller_side must be 'left' or 'right', got {controller_side!r}")

        self.gripper_alpha = float(gripper_alpha)
        self.gripper_max_m = float(gripper_max_m)
        self.enable_skeleton = bool(enable_skeleton)
        self.controller_side = controller_side
        self.name = name

        # ── FK: zero pose / EE reference ──────────────────────────────
        q_zero = np.zeros(6, dtype=float)
        if _ENGINE_DEPS_AVAILABLE:
            self._fk = PiperForwardKinematics(DHType.STANDARD)
            T_ee0 = self._fk.compute_fk(q_zero)
            ee_start = T_ee0[:3, 3].copy()
            r_ee0 = T_ee0[:3, :3].copy()
        else:
            self._fk = None
            ee_start = np.zeros(3, dtype=float)
            r_ee0 = np.eye(3, dtype=float)

        self._q_zero = q_zero
        self._T_zero = np.eye(4, dtype=float)
        self._T_zero[:3, 3] = ee_start
        self._T_zero[:3, :3] = r_ee0

        # ── VR mapper ─────────────────────────────────────────────────
        if _ENGINE_DEPS_AVAILABLE:
            self._mapper = VRToRobotMapper(
                ee_start_pos=ee_start,
                ee_start_rot=r_ee0,
                config=VRMapperConfig(position_scale=1.0),
                debug=False,
            )
        else:
            self._mapper = None

        # ── MuJoCo + MINK IK ──────────────────────────────────────────
        if _ENGINE_DEPS_AVAILABLE:
            self._model = mujoco.MjModel.from_xml_path(PIPER_MJCF_PATH)
            self._data = mujoco.MjData(self._model)
            self._configuration = mink.Configuration(self._model)

            max_vel = np.pi
            self._limits = [
                mink.ConfigurationLimit(model=self._model),
                mink.VelocityLimit(
                    self._model, {f"joint{i}": max_vel for i in range(1, 7)}
                ),
            ]

            ee_task = mink.FrameTask(
                frame_name=MINK_EE_SITE,
                frame_type="site",
                position_cost=1.0,
                orientation_cost=0.3,
                lm_damping=1e-4,
            )
            posture_task = mink.PostureTask(self._model, cost=1e-3)
            self._tasks = [ee_task, posture_task]
            self._solver = "daqp"
            self._rate = RateLimiter(frequency=200.0, warn=False)

            q_rest = self._configuration.q.copy()
            q_rest[:6] = q_zero
            posture_task.set_target(q_rest)

            j7 = self._model.joint("joint7")
            j8 = self._model.joint("joint8")
            self._q_idx7 = int(np.asarray(j7.qposadr).item())
            self._q_idx8 = int(np.asarray(j8.qposadr).item())
        else:
            self._model = None
            self._data = None
            self._configuration = None
            self._tasks = []
            self._limits = []
            self._solver = "daqp"
            self._rate = None
            self._q_idx7 = 6
            self._q_idx8 = 7

        # ── State machine ─────────────────────────────────────────────
        self._mode: str = "RETURNING"
        self._last_q: np.ndarray = q_zero.copy()
        self._hold_target: Optional[np.ndarray] = None
        self._sent_joint_zero: bool = False
        self._prev_squeeze: bool = False
        self._grip_ema: float = 0.0  # 0=open, 1=closed

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        """Current state machine mode (RETURNING / AT_ZERO / TELEOP / HOLD)."""
        return self._mode

    @property
    def last_joint_command(self) -> np.ndarray:
        """Last computed 6-axis joint command (rad)."""
        return self._last_q.copy()

    def _controller_anchor(self, tv) -> Optional[np.ndarray]:
        """Read the controller position anchor from the Vuer handle, if any."""
        if tv is None:
            return None
        try:
            mat = tv.left_controller if self.controller_side == "left" else tv.right_controller
            return np.asarray(mat)[:3, 3].copy()
        except Exception:
            return None

    # ── Main per-frame update ─────────────────────────────────────────────

    def step(self, pose7: np.ndarray, state14: np.ndarray, tv: Any = None) -> dict[str, float]:
        """Advance one control step and return a 7-DoF joint action dict.

        Args:
            pose7: Controller pose [x,y,z,qx,qy,qz,qw] (xyzw quat) in robot frame.
            state14: 14-element controller button/analog state.
            tv: Optional Vuer OpenTeleVision handle for the skeleton overlay.

        Returns:
            {"joint_1.pos": .., ..., "joint_6.pos": .., "gripper.pos": ..} where
            gripper.pos is in meters (0.0 closed, ~gripper_max_m open).
        """
        pose7 = np.asarray(pose7, dtype=float).reshape(-1)
        state14 = np.asarray(state14, dtype=float).reshape(-1)

        # ---- 1. Parse controller state -----------------------------------
        squeeze_holding = float(state14[_IDX_SQUEEZE]) > 0.5
        squeeze_just_pressed = squeeze_holding and not self._prev_squeeze
        return_to_zero = float(state14[_IDX_RETURN]) > 0.5
        trigger_value = float(state14[_IDX_TRIGGER_VALUE])
        self._prev_squeeze = squeeze_holding

        # ---- 2. Gripper EMA from trigger ---------------------------------
        ga = max(0.0, min(1.0, self.gripper_alpha))
        self._grip_ema = ga * trigger_value + (1.0 - ga) * self._grip_ema
        grip_hw = self._grip_ema * 1000.0  # 0-1000 legacy hardware range
        gripper_m = (1.0 - self._grip_ema) * self.gripper_max_m

        # ---- 3. Current EE pose (FK from last commanded q) ---------------
        if self._fk is not None:
            T_cur = self._fk.compute_fk(self._last_q)
        else:
            T_cur = self._T_zero.copy()

        # ---- 4. State machine --------------------------------------------
        target_T = self._T_zero

        if self._mode == "RETURNING":
            if _ENGINE_DEPS_AVAILABLE and joints123_near_zero(self._last_q, tol_deg=5.0):
                self._mode = "AT_ZERO"
                if self._mapper is not None:
                    self._mapper.reset_state(keep_neutral_target=False)
                if not self._sent_joint_zero:
                    self._last_q[:] = 0.0
                    self._sent_joint_zero = True
            target_T = self._T_zero

        elif self._mode == "AT_ZERO":
            target_T = self._T_zero
            if squeeze_just_pressed:
                if self.enable_skeleton and tv is not None:
                    anchor = self._controller_anchor(tv)
                    if anchor is not None:
                        try:
                            tv.enable_skeleton(anchor)
                        except Exception:
                            logger.debug("enable_skeleton failed", exc_info=True)
                if self._mapper is not None:
                    self._mapper.set_neutral(self._T_zero, pose7)
                self._mode = "TELEOP"

        elif self._mode == "HOLD":
            target_T = self._hold_target if self._hold_target is not None else T_cur
            if squeeze_just_pressed:
                if self._mapper is not None:
                    self._mapper.set_neutral(target_T, pose7)
                self._mode = "TELEOP"
            if return_to_zero:
                self._mode = "RETURNING"
                self._sent_joint_zero = False
                if self._mapper is not None and hasattr(self._mapper, "neutral_target_T"):
                    self._mapper.neutral_target_T = None

        elif self._mode == "TELEOP":
            if self._mapper is not None:
                mapped = self._mapper.compute_target_T(pose7)
                target_T = mapped if mapped is not None else T_cur
            else:
                target_T = T_cur
            if not squeeze_holding:
                if tv is not None:
                    try:
                        tv.clear_robot_joints()
                    except Exception:
                        pass
                self._hold_target = T_cur.copy()
                self._mode = "HOLD"

        # ---- 5. MINK IK ---------------------------------------------------
        if self._mode != "AT_ZERO" and self._model is not None:
            try:
                dt = float(self._rate.dt)
                self._last_q = ik_step(
                    self._model,
                    self._data,
                    self._configuration,
                    self._tasks,
                    self._limits,
                    self._solver,
                    dt,
                    self._last_q,
                    target_T,
                    int(grip_hw),
                    self._q_idx7,
                    self._q_idx8,
                    debug_qpos_check=False,
                )
            except Exception:
                logger.debug("MINK IK failed — keeping last_q", exc_info=True)
        elif self._mode == "AT_ZERO":
            self._last_q[:] = 0.0

        # ---- 6. Build action dict ----------------------------------------
        action: dict[str, float] = {
            f"joint_{i + 1}.pos": float(self._last_q[i]) for i in range(6)
        }
        action["gripper.pos"] = float(gripper_m)
        return action
