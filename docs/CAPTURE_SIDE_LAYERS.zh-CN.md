# 数采侧实现分层

本文件固定当前数采侧的实现边界，避免把在线采集主链路、硬件适配层、调试工具和历史
实现混在一起维护。

## 操作员入口

- `scripts/start_vla_capture.sh`：唯一推荐启动入口，负责选择任务、解析 operator/scene，并
  进入 Canonical Raw + MCAP 采集链路。
- `scripts/stop_vla_capture.sh`：唯一推荐停止入口，默认拒绝打断正在录制的 episode。

## 内部采集链路

- `scripts/run_bimanual_mcap_shadow.sh`：内部 wrapper，为操作员入口启用 MCAP sidecar。
- `scripts/run_bimanual_canonical.sh`：内部环境/CAN/Quest 转发启动器。
- `scripts/record_bimanual_canonical.py`：在线数采主循环，负责机器人、Quest、相机、
  dashboard、Canonical Raw writer 和可选 MCAP。

## Source Of Truth

- `canonical_raw/`：在线 episode 的事实格式和写入/校验逻辑。
- `schema/`：Canonical Raw、MCAP topic 和 VLA annotation 的版本化契约。
- `mcap_log/`：可选 shadow log；不能替代 Canonical Raw，也不承担训练重采样。
- `calibration/`：当前 rig 标定快照和来源信息。
- `examples/cleaning_ready_fixture/`：无硬件契约 fixture，用于清洗侧兼容性和 schema smoke。

## 活跃运行时适配层

- `lerobot_robot_bi_piper_quest3/`：当前双臂 Piper 硬件适配层。虽然命名保留 LeRobot，
  但在线采集仍直接依赖它。
- `lerobot_teleoperator_bi_quest3_vr/`：当前双臂 Quest3 VR 适配层。在线采集直接依赖它。
- `teleop/`：Vuer、VR 映射、IK、Piper driver、相机和 runtime helper。只保留被双臂
  适配层或硬件调试路径使用的部分。
- `orbbec_sdk_path.py` 与 `third_party/`：相机 SDK 路径解析和本地 SDK 说明。

## 开发与调试工具

- `scripts/teleop_bimanual_quest3.py`、`scripts/run_bimanual_quest3.sh`：纯遥操作调试，不是
  训练数据 source-of-truth。
- `scripts/run_mink_teleop_quest3.sh`、`scripts/run_teleop_debug_*.sh`、`tools/g1_*`：
  运动学、CAN、关节安全和诊断工具。
- `tools/validate_*`、`tools/inspect_inprogress.py`、`tools/generate_*`：契约验证、恢复检查和
  fixture 生成工具。

## 已移除的 Legacy 路径

- `lerobot_robot_piper_quest3/` 与 `lerobot_teleoperator_quest3_vr/`：旧单臂 LeRobot 插件，
  不在当前双臂 VLA 主路径中。
- `tests/test_mock_recording.py`：旧 LeRobotDataset mock 录制测试，已被 Canonical Raw 和
  cleaning-ready fixture 测试取代。
- `lerobot-record` 在线数采文档：已降级为历史事实，不作为操作员流程或验收 gate。

## 不应进入活仓库的产物

- `backup/`
- `*.bak*`
- `__pycache__/`
- `Log/`
- `MUJOCO_LOG.TXT`
- 临时证书备份，如 `teleop/*.ip-bak`
