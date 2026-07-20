# config.py
from pathlib import Path

# Folder containing config.py = .../TeleVision/teleop
_TELEOP_DIR = Path(__file__).resolve().parent

UDP_IP = "127.0.0.1"
UDP_PORT = 15000

START_POSITION = [0, 0, 0, 0, 0, 0, 0, 0]

RAD_TO_PIPER = 57324.840764  # 1000*180/pi

####### MINK ############
PIPER_MJCF_PATH = str(_TELEOP_DIR / "piper" / "agilex_piper" / "piper.xml")
MINK_EE_SITE = "attachment_site"  # end-effector site name in your MJCF
MINK_SOLVER = "daqp"             
MINK_DT = 0.01                    # IK integration dt (can match config.SLEEP)
MINK_LM_DAMPING = 1e-4
MINK_POSTURE_COST = 1e-3

# Lock the flange (joint6) to suppress wrist singularity.
# When joint5 ~ 0, joint4 and joint6 axes align, so the IK QP thrashes roll
# between joint4/joint6 and those velocities blow up. Freezing joint6 lets the
# remaining 5 axes track the EE target instead (we trade end-effector roll for
# stability). Since start/return leave joint6 at 0 rad, a zero velocity limit
# holds it at 0 rad. Set LOCK_JOINT6_MAX_VEL to a small positive value (e.g.
# 0.05) for a "soft" lock, or LOCK_JOINT6 = False to restore full 6-DoF IK.
LOCK_JOINT6 = True
LOCK_JOINT6_MAX_VEL = 0.0  # rad/s; 0 => hard freeze
#######MINK##############

# === Gripper mapping ===
GRIPPER_MAX_MM = 70.0          # Manual: 0~70mm
GRIPPER_UNIT_MM = 0.001        # SDK: 0.001mm unit
GRIPPER_MAX_UM = int(GRIPPER_MAX_MM / GRIPPER_UNIT_MM)  # 70000

SIM_GRIPPER_RANGE = 0.035      # joint7/8 opening range in MuJoCo