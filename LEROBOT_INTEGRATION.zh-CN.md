# Piper Quest3 VR LeRobot 集成

已成功将 Piper + Quest3 VR 遥操作系统与 LeRobot v0.4.2 集成，用于标准化数据集录制。

## 状态：✅ 完成

所有组件均已正常工作：
- ✅ Conda 环境已修复（mujoco、mink、vuer 以及全部 VR 遥操作依赖）
- ✅ Quest3VR Teleoperator 包（第三方 LeRobot 插件）
- ✅ PiperQuest3 Robot 包（围绕 PIPERFollower 的轻量封装）
- ✅ Mock-VR 录制测试（30 帧，parquet + metadata 已验证）
- ✅ 工厂构造与插件发现
- ✅ 状态机集成（RETURNING → AT_ZERO → TELEOP → HOLD）

## 架构

### Quest3VR Teleoperator（`lerobot_teleoperator_quest3_vr`）
- **VR 通信**：Vuer + Quest3 控制器（姿态 + 按键状态）
- **VR 映射**：末端执行器姿态映射（`VRToRobotMapper`）
- **IK 求解器**：MINK 逆运动学（EE 目标 → 6 轴关节角）
- **状态机**：
  - `RETURNING`：正在向零位移动
  - `AT_ZERO`：等待按下 squeeze 以开始遥操作
  - `TELEOP`：主动 VR 控制（持续按住 squeeze）
  - `HOLD`：位置保持（松开 squeeze）
- **夹爪**：经过 EMA 平滑的 trigger 输入（0-1 → 0-0.07m）
- **Mock 模式**：可在没有 Quest3 的情况下运行，用于测试
- **头显相机画面**：可选开关 `stream_camera_to_headset`（默认 `true`）。设为 `false`
  时跳过共享内存分配和 JPEG 推流，消除与 Orbbec 相机的 CPU/内存争抢，提升数采
  相机FPS稳定性。

### PiperQuest3 Robot（`lerobot_robot_piper_quest3`）
- LeRobot fork 中 `PIPERFollower` 的轻量子类
- 针对 VR 优化的默认值：禁用 EMA 平滑（alpha=1.0）
- Scheme B 录制：在 `send_action()` 后从 follower 编码器读取 action
- 相机集成：3 个相机（前置 + 2 个腕部）

## 录制流程

### LeRobot 录制循环（每帧）
```
1. robot.get_observation() → {joint_N.pos, gripper.pos, cam_*}
2. teleop.set_observation(obs) → 缓存用于 IK
3. teleop.get_action() → VR 控制器 → MINK IK → {joint_N.pos, gripper.pos}
4. robot.send_action(action) → 通过 CAN 总线发送到硬件
5. dataset.add_frame({observation, action, task})
```

## 使用方法

### 1. 测试录制（无硬件）
```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop
python tests/test_mock_recording.py
```

预期输出：包含 parquet + metadata 的 30 帧数据集。

### 2. 真实录制（使用硬件 + Quest3）
```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop

# 单臂示例
#
# 注意：
# - 相机使用 "orbbec" 类型（Orbbec SDK，三相机并发稳定），而非 "opencv"。
#   相机按 serial_number 寻址（重启/换 USB 口不变）。
# - 数据仅保存在本地：--dataset.push_to_hub=false（默认是 true！）+ 显式 --dataset.root。
#   repo_id 只作为本地目录名/元数据，不联网上传。
# - 单臂 VR 用右手手柄，CAN 接口用 can_right。
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

> **💡 提示 — 数采时相机FPS稳定性**：Quest3 头显相机画面（ImageBackground 推流）在
> 后台进程中运行，会对每帧做 JPEG 编码并通过 WebSocket 推送，可能与 3 台 Orbbec
> 相机争抢 CPU 和内存带宽。为确保数采相机FPS稳定，建议关闭头显画面推送：
> ```bash
> --teleop.stream_camera_to_headset=false
> ```
> VR 手柄跟踪和骨架叠加仍正常工作 —— 仅跳过头显中的相机透视画面。

### 3. VR 遥操作控制
- **右手 Squeeze**：进入/退出遥操作模式
  - 按下 Squeeze → 进入 TELEOP（主动 VR 控制）
  - 松开 → 进入 HOLD（位置保持）
- **右手 Trigger**：夹爪控制（模拟量 0-1）
  - 0.0 = 打开（70mm）
  - 1.0 = 闭合（0mm）
- **A 按钮**：返回零位
  - 从 HOLD → RETURNING

### 3b. 双臂（Bimanual）VR 录制

`bi_quest3_vr` 遥操作器同时驱动**两条**臂：**左**手柄控制**左**臂，**右**手柄控制
**右**臂。每条臂各自独立跑一套状态机 + MINK IK。动作为 14 DoF（`left_`/`right_`
键），配合 `bi_piper_quest3` 机器人。控制方式与单臂相同，但**每个手柄各一套**
（各自的 Squeeze / Trigger / A 键，各自的 RETURNING/AT_ZERO/TELEOP/HOLD）。

```bash
conda activate lerobot
cd /home/ylhp-e-ai/ZHITAI_1t/piper-quest3-teleop

# 便捷脚本（可用环境变量覆盖：TASK、NUM_EPISODES、DATASET_ROOT 等）
scripts/record_bimanual_vr.sh

# ……或显式命令：
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

`bi_piper_quest3` 默认已内置 3 台 Orbbec 相机（`cam_front`、`cam_left_wrist`、
`cam_right_wrist`），所以 `--robot.cameras` 可省略。所有相机使用工作区内置的
Orbbec SDK（见下文"Orbbec SDK 独立性"）。

### 4. 状态机流程
```
[Start] → RETURNING（向零位移动）
         ↓（关节接近零位）
         AT_ZERO（等待）
         ↓（按下 squeeze）
         TELEOP（主动控制）
         ↓（松开 squeeze）
         HOLD（位置保持）
         ↓（A 按钮）
         RETURNING
```

## 文件结构

```
piper-quest3-teleop/
├── lerobot_teleoperator_quest3_vr/    # Quest3VR Teleoperator 插件
│   ├── __init__.py
│   ├── config_quest3_vr.py            # Quest3VRConfig（已通过 draccus 注册）
│   └── quest3_vr.py                   # Quest3VR 类（单臂 Teleoperator）
├── lerobot_teleoperator_bi_quest3_vr/ # BiQuest3VR Teleoperator 插件（双臂）
│   ├── __init__.py
│   ├── config_bi_quest3_vr.py         # BiQuest3VRConfig（已注册）
│   └── bi_quest3_vr.py                # BiQuest3VR（两个 ArmVREngine，左+右）
├── lerobot_robot_piper_quest3/        # PiperQuest3 Robot 插件（单臂）
│   ├── __init__.py
│   ├── config_piper_quest3.py         # PiperQuest3Config（默认 Orbbec 相机）
│   └── piper_quest3.py                # PiperQuest3Robot（扩展 PIPERFollower）
├── lerobot_robot_bi_piper_quest3/     # BiPiperQuest3 Robot 插件（双臂）
│   ├── __init__.py
│   ├── config_bi_piper_quest3.py      # BiPiperQuest3Config（3 台 Orbbec 相机）
│   └── bi_piper_quest3.py             # BiPiperQuest3Robot（扩展 BiPiperFollower）
├── orbbec_sdk_path.py                  # 工作区 Orbbec SDK 路径解析器
├── third_party/orbbec_sdk/lib/         # 内置 Orbbec SDK（.so，已 git-ignore）
├── teleop/                             # 现有 VR 遥操作代码（保留并扩展）
│   ├── VuerTeleop.py                  # Vuer/Quest3 通信（+ step_both 双臂）
│   ├── TeleVision.py                  # OpenTeleVision 服务器（+ 左手 pose）
│   ├── Preprocessor.py               # VR 坐标变换（+ process_left/both）
│   ├── vr_arm_engine.py              # 可复用单臂 VR 引擎（映射+IK+状态机）
│   ├── mapping/vr_mapper.py           # VR → EE 姿态映射
│   ├── control/ik_stepper.py          # MINK IK 步进
│   ├── kinematics/                    # FK、DH 参数
│   ├── piper/                         # Piper 驱动（已保留）
│   └── app.py                         # 独立入口（仍可使用）
├── scripts/
│   ├── record_single_arm_vr.sh        # 单臂本地保存启动脚本
│   ├── record_bimanual_vr.sh          # 双臂本地保存启动脚本
│   └── setup_orbbec_sdk.sh            # 重新内置 Orbbec SDK
└── tests/
    └── test_mock_recording.py         # 无硬件测试（单臂 + 双臂）
```

## Orbbec SDK 独立性

相机使用 **Orbbec SDK**（不用 OpenCV —— 本机 3 相机的 OpenCV 并发不稳定）。
LeRobot 的 `OrbbecCameraConfig.sdk_lib_path` 默认指向 `piper_lerobot-main`
fork 内部路径，会让本工作区依赖 fork 的磁盘布局。为保持工作区独立：

- SDK（`libOrbbecSDK.so` + 内部依赖，约 21 MB）已**内置**到
  `third_party/orbbec_sdk/lib/`。它以 `RPATH=$ORIGIN` 构建，整个 lib 目录可任意迁移。
- `orbbec_sdk_path.py` 按以下顺序解析路径：
  `$PIPER_ORBBEC_SDK_LIB` → 工作区内置 → fork 回退（并告警）。
- 所有相机配置（`piper_quest3`、`bi_piper_quest3`）都调用该解析器，因此录制时
  不会为运行时二进制去访问 fork 目录。
- 这些 `.so` 已 git-ignore；全新检出后用 `scripts/setup_orbbec_sdk.sh` 恢复。

> 说明：工作区仍依赖 fork 的 **Python** `lerobot` 包（editable 安装）—— 那是插件
> 基础，属于预期。这里移除的只是 Orbbec **二进制**的耦合。


## 依赖项（已安装在 `lerobot` conda 环境中）

**已存在：**
- lerobot==0.4.2（带 Piper 支持的 LeRobot fork）
- torch、diffusers、datasets、opencv-python-headless
- piper-sdk、python-can

**新安装：**
- mujoco==3.10.0（MuJoCo 物理引擎）
- mink==1.2.0（MINK IK 求解器）
- vuer==0.1.6（VR 流式传输）
- pytransform3d==3.15.0（姿态变换）
- scipy==1.15.3（科学计算）
- loop-rate-limiters==1.2.0（频率控制）
- aiohttp_cors、aiortc（Vuer 依赖）

## 已知限制

1. **需要 Quest3 VR 硬件**：真实录制需要 Quest3 + Vuer 服务器（HTTPS 证书设置）。
2. **Mock VR 模式**：测试模式使用零 VR 姿态（仅用于 CI/测试）。
3. **双臂 VR 已实现**（`bi_quest3_vr` + `bi_piper_quest3`）：左手柄控制左臂，
   右手柄控制右臂，各自独立跑 MINK IK 状态机（14 DoF，`left_`/`right_` 键）。
   注意：头显内的骨架叠加只跟踪单个锚点，双臂使用时**默认关闭**
   （`enable_skeleton=False`）。另有一条非 VR 的双臂路径：
   `--robot.type=bi_piper_follower --teleop.type=piper_drag_teach_keyboard`
   （拖拽示教，抓夹用键盘控制）。
4. **相机设备**：真实录制需连接 3 台 Orbbec 相机（序列号 `CP0BB530000J`、`CC1N16200P0`、
   `CC1N162022N`）。使用 `type: orbbec`（Orbbec SDK）——本机 3 相机的 OpenCV 并发不稳定。
5. **CAN 接口**：需要对应臂的 CAN 接口已启动（单臂：`can_right`；双臂：`can_left` + `can_right`）。
6. **头显相机推流与Orbbec FPS**：默认开启的头显相机推流消耗 CPU 做 JPEG 编码和
   WebSocket 发送，可能导致三台 Orbbec 相机录制时 FPS 不稳定。数采时建议加
   `--teleop.stream_camera_to_headset=false` 关闭。

## 后续步骤

要启用实时录制：
1. **Quest3 设置**：安装 OpenTeleVision VR 应用，配置 Vuer 服务器 HTTPS
2. **CAN 设置**：`sudo ip link set can0 up type can bitrate 1000000`
3. **相机设置**：验证 `/dev/video*` 设备，或使用 Orbbec 相机
4. **独立测试**：`python teleop/teleop_real_arm.py --arm=right`（验证硬件可用）
5. **LeRobot 测试**：运行上面的 `lerobot-record` 命令

## 测试

```bash
# 环境检查
conda activate lerobot
python -c "from lerobot_teleoperator_quest3_vr import Quest3VR; print('✓ Teleoperator OK')"
python -c "from lerobot_robot_piper_quest3 import PiperQuest3Robot; print('✓ Robot OK')"

# 插件发现
python -c "
from lerobot.utils.import_utils import register_third_party_devices
register_third_party_devices()
from lerobot_teleoperator_quest3_vr import Quest3VRConfig
print('✓ quest3_vr type:', Quest3VRConfig().type)
"

# 工厂构造
python -c "
from lerobot.teleoperators.utils import make_teleoperator_from_config
from lerobot_teleoperator_quest3_vr import Quest3VRConfig
t = make_teleoperator_from_config(Quest3VRConfig(mock_vr=True))
print('✓ Factory created:', t.name)
"

# Mock 录制测试
python tests/test_mock_recording.py
```

## 实现总结

**总变更：**
- 2 个新包（创建 6 个文件）
- 1 个测试脚本
- `stream_camera_to_headset` 可选开关，涉及 8 个文件：
  - 2 个配置文件（单臂 + 双臂）
  - 2 个遥操作器文件（单臂 + 双臂）
  - 4 个 `teleop/` 核心文件（`TeleVision.py`、`VuerTeleop.py`、`app.py`、`init_camera.py`）
- 0 个 git commit（开发模式）

**集成方式：**
- LeRobot 第三方插件发现（`lerobot_*` 包名前缀）
- Draccus `@register_subclass` 装饰器用于配置自动注册
- 通过 `make_device_from_device_class` 进行工厂构造
- 使用 `set_observation()` 模式缓存 IK 状态

**录制验证：**
- ✅ 以 10 fps 录制 30 帧
- ✅ 已创建 Parquet 文件（1118 字节，v3 格式）
- ✅ Metadata 文件存在（3 个文件）
- ✅ Action 特征：7 DoF（6 个关节 + 夹爪）
- ✅ Observation 特征：7 DoF（仅状态，mock 模式下无相机）
- ✅ 数据集结构：兼容 LeRobot v3

---

**状态**：已准备好进行真实硬件录制。集成已完成并通过测试。
