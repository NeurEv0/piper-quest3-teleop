#!/usr/bin/env bash
# Dual-arm Quest3 VR teleoperation recording — LOCAL SAVE ONLY.
#
# Left VR controller -> left arm, right controller -> right arm.
# Cameras use the Orbbec SDK (stable 3-camera concurrency), resolved to the
# workspace-vendored SDK. Data is saved locally (push_to_hub=false).
#
# Prereqs:
#   conda activate lerobot
#   both CAN interfaces up:  can_left, can_right
#   Quest3 + Vuer server reachable (HTTPS cert under teleop/)
#   3 Orbbec cameras connected (serials in config_bi_piper_quest3.py)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Ensure custom lerobot_robot_*/lerobot_teleoperator_* packages are discoverable.
export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"

# Override these via environment as needed.
TASK="${TASK:-stack the cube}"
NUM_EPISODES="${NUM_EPISODES:-20}"
EPISODE_TIME_S="${EPISODE_TIME_S:-60}"
RESET_TIME_S="${RESET_TIME_S:-30}"
REPO_ID="${REPO_ID:-local/piper_bimanual_vr_demo}"
DATASET_ROOT="${DATASET_ROOT:-/home/ylhp-e-ai/ZHITAI_1t/piper_lerobot-data/bimanual_vr_demo}"
LEFT_CAN="${LEFT_CAN:-can_left}"
RIGHT_CAN="${RIGHT_CAN:-can_right}"

lerobot-record \
  --robot.type=bi_piper_quest3 \
  --robot.left_can_name="${LEFT_CAN}" \
  --robot.right_can_name="${RIGHT_CAN}" \
  --teleop.type=bi_quest3_vr \
  --teleop.mock_vr=false \
  --display_data=false \
  --dataset.repo_id="${REPO_ID}" \
  --dataset.root="${DATASET_ROOT}" \
  --dataset.push_to_hub=false \
  --dataset.num_episodes="${NUM_EPISODES}" \
  --dataset.episode_time_s="${EPISODE_TIME_S}" \
  --dataset.reset_time_s="${RESET_TIME_S}" \
  --dataset.single_task="${TASK}"

echo "Done. Dataset saved locally under: ${DATASET_ROOT}"
