#!/usr/bin/env bash
# Single-arm Quest3 VR teleoperation recording — LOCAL SAVE ONLY.
#
# Right VR controller -> right Piper arm. Cameras (front + right wrist) use the
# Orbbec SDK, resolved to the workspace-vendored SDK. Data saved locally.
#
# Prereqs:
#   conda activate lerobot
#   CAN interface up:  can_right
#   Quest3 + Vuer server reachable (HTTPS cert under teleop/)
#   2 Orbbec cameras connected (serials in config_piper_quest3.py)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Ensure custom lerobot_robot_*/lerobot_teleoperator_* packages are discoverable.
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

TASK="${TASK:-Pick and place the cube}"
NUM_EPISODES="${NUM_EPISODES:-20}"
EPISODE_TIME_S="${EPISODE_TIME_S:-60}"
RESET_TIME_S="${RESET_TIME_S:-30}"
REPO_ID="${REPO_ID:-local/piper_quest3_demo}"
DATASET_ROOT="${DATASET_ROOT:-/home/ylhp-e-ai/ZHITAI_1t/piper_lerobot-data/quest3_demo}"
CAN="${CAN:-can_right}"

lerobot-record \
  --robot.type=piper_quest3 \
  --robot.can_name="${CAN}" \
  --robot.teleop_joint_alpha=1.0 \
  --robot.teleop_gripper_alpha=1.0 \
  --teleop.type=quest3_vr \
  --teleop.mock_vr=false \
  --teleop.stream_camera_to_headset=false \
  --display_data=true \
  --dataset.repo_id="${REPO_ID}" \
  --dataset.root="${DATASET_ROOT}" \
  --dataset.push_to_hub=false \
  --dataset.num_episodes="${NUM_EPISODES}" \
  --dataset.episode_time_s="${EPISODE_TIME_S}" \
  --dataset.reset_time_s="${RESET_TIME_S}" \
  --dataset.single_task="${TASK}"

echo "Done. Dataset saved locally under: ${DATASET_ROOT}"
