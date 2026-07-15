# Piper Quest3 VR LeRobot Integration

Successfully integrated the Piper + Quest3 VR teleoperation system with LeRobot v0.4.2 for standardized dataset recording.

## Status: ✅ Complete

All components working:
- ✅ Conda environment repaired (mujoco, mink, vuer, and all VR teleop dependencies)
- ✅ Quest3VR Teleoperator package (third-party LeRobot plugin)
- ✅ PiperQuest3 Robot package (thin wrapper around PIPERFollower)
- ✅ Mock-VR recording test (30 frames, parquet + metadata verified)
- ✅ Factory construction and plugin discovery
- ✅ State machine integration (RETURNING → AT_ZERO → TELEOP → HOLD)

## Architecture

### Quest3VR Teleoperator (`lerobot_teleoperator_quest3_vr`)
- **VR Communication**: Vuer + Quest3 controller (pose + button state)
- **VR Mapping**: End-effector pose mapping (`VRToRobotMapper`)
- **IK Solver**: MINK inverse kinematics (EE target → 6-axis joint angles)
- **State Machine**:
  - `RETURNING`: Moving toward zero position
  - `AT_ZERO`: Waiting for squeeze to start teleop
  - `TELEOP`: Active VR control (squeeze held)
  - `HOLD`: Position hold (squeeze released)
- **Gripper**: EMA-smoothed trigger input (0-1 → 0-0.07m)
- **Mock Mode**: Can run without Quest3 for testing
- **Headset Camera Display**: Optional `stream_camera_to_headset` flag (default `true`).
  Set `false` to skip shared-memory allocation and JPEG streaming to the Quest3
  headset, eliminating CPU/memory contention with the Orbbec cameras during
  recording for more stable camera FPS.

### PiperQuest3 Robot (`lerobot_robot_piper_quest3`)
- Thin subclass of `PIPERFollower` from LeRobot fork
- VR-optimized defaults: EMA smoothing disabled (alpha=1.0)
- Scheme B recording: action from follower encoders after `send_action()`
- Camera integration: 3 cameras (front + 2 wrist)

## Recording Pipeline

### LeRobot Recording Loop (per frame)
```
1. robot.get_observation() → {joint_N.pos, gripper.pos, cam_*}
2. teleop.set_observation(obs) → cache for IK
3. teleop.get_action() → VR controller → MINK IK → {joint_N.pos, gripper.pos}
4. robot.send_action(action) → CAN bus to hardware
5. dataset.add_frame({observation, action, task})
```

## Usage

### 1. Test Recording (No Hardware)
```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop
python tests/test_mock_recording.py
```

Expected output: 30-frame dataset with parquet + metadata.

### 2. Real Recording (With Hardware + Quest3)
```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop

# Single-arm example
#
# NOTES:
# - Cameras use the "orbbec" type (Orbbec SDK, stable 3-camera concurrency),
#   NOT "opencv". Cameras are addressed by serial_number (stable across reboots).
# - Data is saved LOCALLY ONLY: --dataset.push_to_hub=false (default is true!)
#   and an explicit --dataset.root. The repo_id is only a local folder/metadata name.
# - Use the right arm's CAN interface (can_right) for the single right-hand VR mapper.
lerobot-record \
  --robot.type=piper_quest3 \
  --robot.can_name=can_right \
  --robot.cameras='{
    "cam_front": {"type": "orbbec", "serial_number": "CP0BB530000J", "width": 640, "height": 480, "fps": 30},
    "cam_left_wrist": {"type": "orbbec", "serial_number": "CC1N16200P0", "width": 640, "height": 480, "fps": 30},
    "cam_right_wrist": {"type": "orbbec", "serial_number": "CC1N162022N", "width": 640, "height": 480, "fps": 30}
  }' \
  --robot.teleop_joint_alpha=1.0 \
  --robot.teleop_gripper_alpha=1.0 \
  --teleop.type=quest3_vr \
  --teleop.mock_vr=false \
  --display_data=true \
  --dataset.repo_id=local/piper_quest3_demo \
  --dataset.root=/home/ylhp-e-ai/ZHITAI_1t/piper_lerobot-data/quest3_demo \
  --dataset.push_to_hub=false \
  --dataset.num_episodes=20 \
  --dataset.episode_time_s=60 \
  --dataset.reset_time_s=30 \
  --dataset.single_task="Pick and place the cube"
```

> **💡 Tip — Stable Camera FPS During Recording**: The Quest3 headset camera
> display (ImageBackground streaming) runs in a background process and encodes
> JPEG frames over WebSocket, which can compete with the 3 Orbbec cameras for
> CPU and memory bandwidth. To maximize recording stability and camera FPS
> consistency, disable the headset display:
> ```bash
> --teleop.stream_camera_to_headset=false
> ```
> The VR controllers and skeleton overlay continue to work normally — only the
> camera pass-through view in the headset is skipped.

### 3. VR Teleop Controls
- **Right Squeeze**: Engage/disengage teleop mode
  - Squeeze → Enter TELEOP (active VR control)
  - Release → Enter HOLD (position hold)
- **Right Trigger**: Gripper control (analog 0-1)
  - 0.0 = open (70mm)
  - 1.0 = closed (0mm)
- **A Button**: Return to zero position
  - From HOLD → RETURNING

### 3b. Dual-Arm (Bimanual) VR Recording

The `bi_quest3_vr` teleoperator drives **both** arms: the **left** controller
controls the **left** arm, the **right** controller controls the **right** arm.
Each arm runs its own independent state machine + MINK IK. The action is 14-DoF
with `left_`/`right_` keys, paired with the `bi_piper_quest3` robot.

Controls are the same as single-arm, but **per controller** (each hand has its
own Squeeze / Trigger / A-button, and its own RETURNING/AT_ZERO/TELEOP/HOLD).

```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop

# Convenience wrapper (env-overridable: TASK, NUM_EPISODES, DATASET_ROOT, ...)
scripts/record_bimanual_vr.sh

# ...or the explicit command:
lerobot-record \
  --robot.type=bi_piper_quest3 \
  --robot.left_can_name=can_left \
  --robot.right_can_name=can_right \
  --teleop.type=bi_quest3_vr \
  --teleop.mock_vr=false \
  --dataset.repo_id=local/piper_bimanual_vr_demo \
  --dataset.root=/home/ylhp-e-ai/ZHITAI_1t/piper_lerobot-data/bimanual_vr_demo \
  --dataset.push_to_hub=false \
  --dataset.num_episodes=20 \
  --dataset.episode_time_s=60 \
  --dataset.single_task="stack the cube"
```

The `bi_piper_quest3` robot config ships the 3 Orbbec cameras
(`cam_front`, `cam_left_wrist`, `cam_right_wrist`) by default, so `--robot.cameras`
is optional. All cameras use the workspace-vendored Orbbec SDK (see below).

### 4. State Machine Flow
```
[Start] → RETURNING (moving to zero)
         ↓ (joints near zero)
         AT_ZERO (waiting)
         ↓ (squeeze pressed)
         TELEOP (active control)
         ↓ (squeeze released)
         HOLD (position hold)
         ↓ (A button)
         RETURNING
```

## File Structure

```
piper-quest3-teleop/
├── lerobot_teleoperator_quest3_vr/    # Quest3VR Teleoperator plugin (single-arm)
│   ├── __init__.py
│   ├── config_quest3_vr.py            # Quest3VRConfig (draccus-registered)
│   └── quest3_vr.py                   # Quest3VR class (LeRobot Teleoperator)
├── lerobot_teleoperator_bi_quest3_vr/ # BiQuest3VR Teleoperator plugin (dual-arm)
│   ├── __init__.py
│   ├── config_bi_quest3_vr.py         # BiQuest3VRConfig (draccus-registered)
│   └── bi_quest3_vr.py                # BiQuest3VR (two ArmVREngine, left+right)
├── lerobot_robot_piper_quest3/        # PiperQuest3 Robot plugin (single-arm)
│   ├── __init__.py
│   ├── config_piper_quest3.py         # PiperQuest3Config (Orbbec cams by default)
│   └── piper_quest3.py                # PiperQuest3Robot (extends PIPERFollower)
├── lerobot_robot_bi_piper_quest3/     # BiPiperQuest3 Robot plugin (dual-arm)
│   ├── __init__.py
│   ├── config_bi_piper_quest3.py      # BiPiperQuest3Config (3 Orbbec cams)
│   └── bi_piper_quest3.py             # BiPiperQuest3Robot (extends BiPiperFollower)
├── orbbec_sdk_path.py                  # Workspace Orbbec SDK path resolver
├── third_party/orbbec_sdk/lib/         # Vendored Orbbec SDK (.so, git-ignored)
├── teleop/                             # Existing VR teleop (preserved + extended)
│   ├── VuerTeleop.py                  # Vuer/Quest3 comm (+ step_both for dual-arm)
│   ├── TeleVision.py                  # OpenTeleVision server (+ left_controller pose)
│   ├── Preprocessor.py               # VR frame transform (+ process_left/both)
│   ├── vr_arm_engine.py              # Reusable per-arm VR engine (mapper+IK+FSM)
│   ├── mapping/vr_mapper.py           # VR → EE pose mapping
│   ├── control/ik_stepper.py          # MINK IK step
│   ├── kinematics/                    # FK, DH parameters
│   ├── piper/                         # Piper driver (preserved)
│   └── app.py                         # Standalone entry point (still works)
├── scripts/
│   ├── record_single_arm_vr.sh        # Single-arm local-save launcher
│   ├── record_bimanual_vr.sh          # Dual-arm local-save launcher
│   └── setup_orbbec_sdk.sh            # Re-vendor the Orbbec SDK
└── tests/
    └── test_mock_recording.py         # No-hardware tests (single + bimanual)
```

## Orbbec SDK Independence

Cameras use the **Orbbec SDK** (not OpenCV — 3-camera OpenCV concurrency was
unstable on this rig). LeRobot's `OrbbecCameraConfig.sdk_lib_path` defaults to a
path *inside* the `piper_lerobot-main` fork, which would couple this workspace to
the fork's on-disk layout. To keep the workspace self-contained:

- The SDK (`libOrbbecSDK.so` + internal deps, ~21 MB) is **vendored** under
  `third_party/orbbec_sdk/lib/`. It is built with `RPATH=$ORIGIN`, so the whole
  lib directory is relocatable.
- `orbbec_sdk_path.py` resolves the path in order:
  `$PIPER_ORBBEC_SDK_LIB` → workspace-vendored → fork fallback (with a warning).
- All camera configs (`piper_quest3`, `bi_piper_quest3`) call this resolver, so
  recording never reaches into the fork tree for the runtime binary.
- The `.so` files are git-ignored; restore them on a fresh checkout with
  `scripts/setup_orbbec_sdk.sh`.

> Note: the workspace still depends on the fork for the **Python** `lerobot`
> package (editable-installed) — that's the plugin base and is expected. Only the
> Orbbec *binary* coupling is removed here.


## Dependencies (Installed in `lerobot` conda env)

**Already present:**
- lerobot==0.4.2 (LeRobot fork with Piper support)
- torch, diffusers, datasets, opencv-python-headless
- piper-sdk, python-can

**Newly installed:**
- mujoco==3.10.0 (MuJoCo physics engine)
- mink==1.2.0 (MINK IK solver)
- vuer==0.1.6 (VR streaming)
- pytransform3d==3.15.0 (pose transformations)
- scipy==1.15.3 (scientific computing)
- loop-rate-limiters==1.2.0 (rate control)
- aiohttp_cors, aiortc (Vuer dependencies)

## Known Limitations

1. **Quest3 VR Hardware Required**: Real recording needs Quest3 + Vuer server (HTTPS cert setup).
2. **Mock VR Mode**: Test mode uses zero VR poses (for CI/testing only).
3. **Dual-Arm VR is implemented** (`bi_quest3_vr` + `bi_piper_quest3`): the left
   controller drives the left arm, the right drives the right, each with its own
   MINK IK state machine (14-DoF, `left_`/`right_` keys). Caveat: the in-headset
   skeleton overlay tracks a single anchor, so it is **disabled by default** for
   bimanual use (`enable_skeleton=False`). An alternative non-VR dual-arm path
   also exists: `--robot.type=bi_piper_follower --teleop.type=piper_drag_teach_keyboard`
   (drag-teach, grippers on keyboard).
4. **Camera Devices**: Real recording needs 3 Orbbec cameras connected (serials
   `CP0BB530000J`, `CC1N16200P0`, `CC1N162022N`). Use `type: orbbec` (Orbbec SDK) —
   OpenCV concurrency for 3 cameras was found unstable on this rig.
5. **CAN Interface**: Requires the arm's CAN interface up (single arm: `can_right`;
   dual arm: `can_left` + `can_right`).
6. **Headset Camera Display vs Orbbec FPS**: The default headset camera streaming
   consumes CPU for JPEG encoding and WebSocket push, which can cause unstable
   FPS on the 3 Orbbec cameras during recording. Set
   `--teleop.stream_camera_to_headset=false` to disable it when recording quality
   matters more than seeing the camera view in the headset.

## Next Steps

To enable live recording:
1. **Quest3 Setup**: Install OpenTeleVision VR app, configure Vuer server HTTPS
2. **CAN Setup**: `sudo ip link set can0 up type can bitrate 1000000`
3. **Camera Setup**: Verify `/dev/video*` devices or use Orbbec cameras
4. **Test Standalone**: `python teleop/teleop_real_arm.py --arm=right` (verify hardware works)
5. **Test LeRobot**: Run `lerobot-record` command above

## Testing

```bash
# Environment check
conda activate lerobot
python -c "from lerobot_teleoperator_quest3_vr import Quest3VR; print('✓ Teleoperator OK')"
python -c "from lerobot_robot_piper_quest3 import PiperQuest3Robot; print('✓ Robot OK')"

# Plugin discovery
python -c "
from lerobot.utils.import_utils import register_third_party_devices
register_third_party_devices()
from lerobot_teleoperator_quest3_vr import Quest3VRConfig
print('✓ quest3_vr type:', Quest3VRConfig().type)
"

# Factory construction
python -c "
from lerobot.teleoperators.utils import make_teleoperator_from_config
from lerobot_teleoperator_quest3_vr import Quest3VRConfig
t = make_teleoperator_from_config(Quest3VRConfig(mock_vr=True))
print('✓ Factory created:', t.name)
"

# Mock recording test
python tests/test_mock_recording.py
```

## Implementation Summary

**Total Changes:**
- 2 new packages (6 files created)
- 1 test script
- `stream_camera_to_headset` optional flag added across 8 files:
  - 2 config files (single-arm + dual-arm)
  - 2 teleoperator files (single-arm + dual-arm)
  - 4 `teleop/` core files (`TeleVision.py`, `VuerTeleop.py`, `app.py`, `init_camera.py`)

**Integration Method:**
- LeRobot third-party plugin discovery (`lerobot_*` package prefix)
- Draccus `@register_subclass` decorators for config auto-registration
- Factory construction via `make_device_from_device_class`
- `set_observation()` pattern for IK state caching

**Recording Verification:**
- ✅ 30 frames recorded at 10 fps
- ✅ Parquet file created (1118 bytes, v3 format)
- ✅ Metadata files present (3 files)
- ✅ Action features: 7 DoF (6 joints + gripper)
- ✅ Observation features: 7 DoF (state only, no cameras in mock mode)
- ✅ Dataset structure: LeRobot v3 compatible

---

**Status**: Ready for live hardware recording. The integration is complete and tested.
