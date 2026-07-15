import mujoco
import mink
import numpy as np
from .. import config

def reset_to_zero_like_init(rt, q_zero: np.ndarray):
    q_zero = np.asarray(q_zero, dtype=float).reshape(6,)

    rt.last_q[:6] = q_zero.copy()

    # Zero mujoco state first (for FK/Jacobian)
    for k in range(6):
        j = rt.model.joint(f"joint{k+1}")
        adr = int(np.asarray(j.qposadr).item())
        rt.data.qpos[adr] = float(q_zero[k])
        vadr = int(np.asarray(j.dofadr).item())
        rt.data.qvel[vadr] = 0.0
    mujoco.mj_forward(rt.model, rt.data)

    # Create new mink configuration (completely remove past state)
    rt.configuration = mink.Configuration(rt.model)

    q_full = rt.configuration.q.copy()
    q_full[:6] = q_zero
    rt.configuration.q[:] = q_full

    # Also recreate tasks
    ee_task = mink.FrameTask(
        frame_name=config.MINK_EE_SITE,  
        frame_type="site",
        position_cost=1.0,
        orientation_cost=0.3,
        lm_damping=float(getattr(config, "MINK_LM_DAMPING", 1e-6)),
    )
    posture_task = mink.PostureTask(
        rt.model,
        cost=float(getattr(config, "MINK_POSTURE_COST", 1e-3))
    )
    rt.tasks = [ee_task, posture_task]

    # Fix posture target = q_zero
    q_rest_full = rt.configuration.q.copy()
    q_rest_full[:6] = q_zero
    posture_task.set_target(q_rest_full)

    # Also reset filters
    rt.T_filt = None
    rt.vel_filt = None