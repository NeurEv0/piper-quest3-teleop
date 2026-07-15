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
#######MINK##############

# === Gripper mapping ===
GRIPPER_MAX_MM = 70.0          # Manual: 0~70mm
GRIPPER_UNIT_MM = 0.001        # SDK: 0.001mm unit
GRIPPER_MAX_UM = int(GRIPPER_MAX_MM / GRIPPER_UNIT_MM)  # 70000

SIM_GRIPPER_RANGE = 0.035      # joint7/8 opening range in MuJoCo