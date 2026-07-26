# VR 双臂遥操作运行时架构

Updated: 2026-07-26

本文档只描述当前仍服务在线双臂 VLA 数采的 Quest3/VR 运行时路径。在线录制入口为
`scripts/start_vla_capture.sh`；Canonical Raw 是训练前清洗的 source of truth。旧的
`lerobot-record` 数据集录制命令、旧单臂 `quest3_vr` 插件和单臂 mock recording 测试已
从活跃仓库移除。

## 当前入口

| 层级 | 入口 | 职责 |
| --- | --- | --- |
| 操作员启动 | `scripts/start_vla_capture.sh` | 选择任务、operator、scene，并进入 Canonical Raw + MCAP 链路 |
| 操作员停止 | `scripts/stop_vla_capture.sh` | 安全停止采集服务，默认拒绝打断正在录制的 episode |
| 内部启动器 | `scripts/run_bimanual_canonical.sh` | 配置 conda/PYTHONPATH/CAN/Quest USB 转发并启动 Python 主循环 |
| 在线主循环 | `scripts/record_bimanual_canonical.py` | 连接 Quest、双臂、相机、dashboard、Canonical Raw writer 和 MCAP sidecar |
| 纯遥操作调试 | `scripts/teleop_bimanual_quest3.py` | 不落训练数据，仅用于双臂 VR 遥操作诊断 |

## 数据流

```text
Quest3 controllers
  -> teleop.VuerTeleop / teleop.TeleVision
  -> lerobot_teleoperator_bi_quest3_vr.BiQuest3VR
  -> teleop.vr_arm_engine.ArmVREngine x 2
  -> 14 DoF action
  -> lerobot_robot_bi_piper_quest3.BiPiperQuest3
  -> Piper CAN command + robot feedback
  -> canonical_raw.AsyncCanonicalRecorder
```

相机路径独立于 Quest 预览：

```text
Orbbec cam_front / cam_left_wrist / cam_right_wrist
  -> lerobot camera configs
  -> canonical_raw.SwitchableCameraManager
  -> canonical_raw.SynchronizedCameraRecorder
  -> camera_*.mp4 + camera_timestamps.parquet
```

Quest3 头显看到的是人类预览 buffer。预览上的 `READY`、`REC`、`FINALIZING` 等字样只写
入头显显示，不烧录到训练相机视频。

## 核心模块

- `teleop/TeleVision.py`：Vuer/OpenTeleVision 服务和 WebXR session。
- `teleop/VuerTeleop.py`：读取左右 Quest controller pose/button state，维护共享图像 buffer。
- `teleop/vr_arm_engine.py`：单臂 VR-to-arm 状态机、IK、gripper 平滑和诊断；双臂路径实例化两套。
- `teleop/mapping/vr_mapper.py`：VR 控制器相对运动到机器人末端目标的映射。
- `teleop/control/ik_stepper.py`：MINK IK 步进。
- `lerobot_teleoperator_bi_quest3_vr/`：双臂 Quest3 runtime adapter。
- `lerobot_robot_bi_piper_quest3/`：双臂 Piper runtime adapter。
- `canonical_raw/`：在线落盘、同步、恢复、dashboard 和 validator。

## 手柄语义

每个手柄独立控制同侧手臂：

- 左手柄 -> 左臂。
- 右手柄 -> 右臂。
- Squeeze：进入 TELEOP，松开后 HOLD。
- Trigger：控制夹爪。
- A/X 组合按键同时还承担录制或显示切换手势，具体以
  `scripts/record_bimanual_canonical.py` 中的 `VRRecordingGesture` 和
  `VRVideoDisplayGesture` 为准。

每条臂的控制状态机：

```text
RETURNING -> AT_ZERO -> TELEOP -> HOLD -> RETURNING
```

## Quest3 连接

推荐由 `scripts/start_vla_capture.sh` 间接触发 `scripts/quest3_usb_link.sh` 配置 USB
转发。头显浏览器访问：

```text
https://localhost:8012?ws=wss://localhost:8012
```

如果使用 WiFi 直连，需要主机和 Quest3 在同一局域网，并使用 Vuer HTTPS/WSS 地址。证书
仍由 `teleop/cert.pem` 和 `teleop/key.pem` 提供；这些文件不应提交到仓库。

## 不再维护的路径

- 单臂 `lerobot_teleoperator_quest3_vr/`。
- 单臂 `lerobot_robot_piper_quest3/`。
- `tests/test_mock_recording.py` 中的 `LeRobotDataset` mock recorder。
- 文档中的 `lerobot-record` 在线数采命令。

需要训练集时，先采 Canonical Raw，再由离线 exporter 派生 LeRobot 或其它训练格式。
