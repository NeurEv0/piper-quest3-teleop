# LeRobot 边界与运行时适配层

Updated: 2026-07-26

本文档记录当前数采侧对 LeRobot 的真实边界。在线 VLA 数采的 source of truth 是
Canonical Raw + 可选 MCAP，操作员入口是 `scripts/start_vla_capture.sh` 和
`scripts/stop_vla_capture.sh`。本仓库不再维护 `lerobot-record` 作为在线录制入口，也不再
维护旧单臂 `piper_quest3` / `quest3_vr` 插件。

## 当前结论

- `LeRobotDataset` 不参与在线数采落盘。
- `lerobot-record` 旧录制路径已退出活跃仓库，不作为操作员流程、测试 gate 或文档推荐命令。
- 双臂运行时仍依赖 LeRobot fork 的设备抽象和相机构造工具。
- 保留的 `lerobot_*` 目录应理解为 runtime adapters，而不是数据集录制产品面。

## 保留的活跃适配层

### `lerobot_robot_bi_piper_quest3/`

双臂 Piper 运行时适配层。`scripts/record_bimanual_canonical.py` 直接使用
`BiPiperQuest3` 和 `BiPiperQuest3Config` 连接双 CAN、读取机器人反馈，并执行
`send_action()`。它仍通过 LeRobot fork 的 `BiPiperFollower` 访问底层硬件，但输出被
Canonical Raw recorder 接管。

### `lerobot_teleoperator_bi_quest3_vr/`

双臂 Quest3 VR 运行时适配层。`BiQuest3VR` 负责读取两个 Quest 控制器、运行左右两套
VR-to-arm 状态机和 IK，并向在线 recorder 暴露 14 DoF action 与原始 VR sample。它不写
LeRobot dataset。

### `lerobot.cameras.utils.make_cameras_from_configs`

当前三路 Orbbec 相机仍复用 LeRobot 的相机构造函数。相机数据由
`canonical_raw.cameras.SynchronizedCameraRecorder` 同步写入 Canonical Raw，不走
`LeRobotDataset.add_frame()`。

## 已移除的旧路径

- `lerobot_robot_piper_quest3/`
- `lerobot_teleoperator_quest3_vr/`
- `tests/test_mock_recording.py`

这些文件只服务旧单臂插件和旧 LeRobotDataset mock 录制，不在当前双臂 Canonical Raw 主
路径中。后续若需要单臂在线录制，应按 Canonical Raw 主链路重新实现，而不是恢复旧
`lerobot-record` 路线。

## 当前在线录制数据流

```text
start_vla_capture.sh
  -> run_bimanual_mcap_shadow.sh
  -> run_bimanual_canonical.sh
  -> record_bimanual_canonical.py
       -> BiQuest3VR / BiPiperQuest3 / Orbbec cameras
       -> AsyncCanonicalRecorder
       -> canonical_raw episode + optional raw.mcap
```

每一帧在线循环保留四类事实：

- requested/sent action 与控制生命周期时间戳。
- robot feedback 与机器人读回时间戳。
- Quest3 左右手柄 pose/button 状态和 freshness。
- 三路相机独立视频、时间戳和同步组 ID。

训练用 LeRobot dataset 若仍需要，应由后续离线 exporter 从 Canonical Raw 派生，不能把
在线 recorder 重新切回 `LeRobotDataset`。

## 验收

清洗本层边界后，运行：

```bash
python tools/check_capture_cleanup_gate.py
```

该 gate 会确认旧单臂插件和旧 LeRobotDataset 测试不再存在，活跃代码不再引用
`lerobot-record` / `LeRobotDataset`，并验证操作员入口和默认落盘路径。
