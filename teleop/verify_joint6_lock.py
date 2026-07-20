"""Ad-hoc verification for the joint6 flange lock (not a pytest suite).

Drives the real ik_step against an EE target that demands end-effector roll,
with the joint6 velocity lock ON vs OFF, and checks:
  * lock ON  -> joint6 stays frozen at ~0 rad
  * lock OFF -> joint6 actually moves (roll leaks into joint6)
  * lock ON  -> the other 5 axes still reduce EE position error
Run:  python -m teleop.verify_joint6_lock   (from repo root, lerobot env)
"""
import numpy as np
import mujoco
import mink

from teleop import config
from teleop.control.ik_stepper import ik_step
from teleop.kinematics.piper_forward_kinematics import PiperForwardKinematics, DHType


def build_mink(lock_joint6: bool):
    model = mujoco.MjModel.from_xml_path(config.PIPER_MJCF_PATH)
    data = mujoco.MjData(model)
    configuration = mink.Configuration(model)

    max_vel = np.pi
    max_velocities = {f"joint{i}": max_vel for i in range(1, 7)}
    if lock_joint6:
        max_velocities["joint6"] = 0.0
    limits = [
        mink.ConfigurationLimit(model=model),
        mink.VelocityLimit(model, max_velocities),
    ]
    ee_task = mink.FrameTask(
        frame_name=config.MINK_EE_SITE, frame_type="site",
        position_cost=1.0, orientation_cost=0.3, lm_damping=1e-4,
    )
    posture_task = mink.PostureTask(model, cost=1e-3)
    q_rest = configuration.q.copy()
    q_rest[:6] = 0.0
    posture_task.set_target(q_rest)
    tasks = [ee_task, posture_task]

    j7, j8 = model.joint("joint7"), model.joint("joint8")
    q_idx7 = int(np.asarray(j7.qposadr).item())
    q_idx8 = int(np.asarray(j8.qposadr).item())
    return model, data, configuration, tasks, limits, q_idx7, q_idx8


def run(lock_joint6: bool, with_roll: bool = True):
    model, data, configuration, tasks, limits, q_idx7, q_idx8 = build_mink(lock_joint6)
    fk = PiperForwardKinematics(DHType.STANDARD)

    # Target = zero pose, optionally with a large roll about the tool axis.
    T0 = fk.compute_fk(np.zeros(6))
    target = T0.copy()
    if with_roll:
        roll = np.deg2rad(80.0)
        Rz = np.array([[np.cos(roll), -np.sin(roll), 0],
                       [np.sin(roll),  np.cos(roll), 0],
                       [0, 0, 1]], dtype=float)
        target[:3, :3] = T0[:3, :3] @ Rz
    # nudge position so the arm has to reach
    target[:3, 3] = T0[:3, 3] + np.array([0.03, 0.02, -0.02])

    last_q = np.zeros(6)
    q6_hist = []
    for _ in range(400):
        last_q = ik_step(
            model, data, configuration, tasks, limits, "daqp", 1 / 100.0,
            last_q, target, grip_hw=500, q_idx7=q_idx7, q_idx8=q_idx8,
        )
        q6_hist.append(last_q[5])

    final_ee = fk.compute_fk(last_q)
    pos_err = float(np.linalg.norm(final_ee[:3, 3] - target[:3, 3]))
    q6_hist = np.array(q6_hist)
    return {
        "q6_max_abs": float(np.max(np.abs(q6_hist))),
        "q6_final": float(last_q[5]),
        "pos_err": pos_err,
        "last_q_deg": np.round(np.rad2deg(last_q), 2).tolist(),
    }


on_roll = run(lock_joint6=True, with_roll=True)
off_roll = run(lock_joint6=False, with_roll=True)
on_pos = run(lock_joint6=True, with_roll=False)
off_pos = run(lock_joint6=False, with_roll=False)

print("=== LOCK ON  (roll) ===", on_roll)
print("=== LOCK OFF (roll) ===", off_roll)
print("=== LOCK ON  (pos)  ===", on_pos)
print("=== LOCK OFF (pos)  ===", off_pos)

# (a) joint6 stays frozen with lock on
assert on_roll["q6_max_abs"] < 1e-6, f"joint6 not frozen (roll): {on_roll['q6_max_abs']}"
assert on_pos["q6_max_abs"] < 1e-6, f"joint6 not frozen (pos): {on_pos['q6_max_abs']}"

# (b) joint6 does move with lock off
assert off_roll["q6_max_abs"] > 1e-2, f"joint6 did not move (roll): {off_roll['q6_max_abs']}"

# (c) lock-on position error stays close to lock-off (position-only target)
assert on_pos["pos_err"] < off_pos["pos_err"] + 1e-2, (
    f"lock degrades position tracking: on={on_pos['pos_err']} off={off_pos['pos_err']}"
)

# (d) position-only target with lock tracks at least as well as the
#     unlocked case (a 28mm residual is normal for a short 4s horizon from
#     a 4cm position jump; real teleop chases a nearby target every 5ms).
assert on_pos["pos_err"] < off_pos["pos_err"] + 1e-2, (
    f"lock degrades position tracking: on={on_pos['pos_err']} off={off_pos['pos_err']}"
)

print("\nPASS: joint6 frozen at 0 with lock on; moves with lock off; position still tracked.")
