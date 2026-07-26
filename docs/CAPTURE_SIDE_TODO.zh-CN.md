# 数采侧 Cleaning-Ready TODO

Updated: 2026-07-26

本清单只保留数采侧仍需交付、且直接影响 data_postprocess session-workflow 的事项。Canonical Raw 是 source of truth；MCAP 只是可选 shadow log，不承担训练重采样、切分或 policy action 派生。
数采侧活跃实现边界见 `docs/CAPTURE_SIDE_LAYERS.zh-CN.md`。

## 当前契约基线

- Storage schema：piper_canonical_raw_v1
- Capture contract：piper_capture_cleaning_ready_v1
- Action semantics：piper_action_semantics_v1
- 主时间基：host_monotonic_ns
- 核心流：control、robot_feedback、vr_input、camera_timestamps
- finalized episode 至少提供 metadata、核心表、事件、manifest 和 validation；关闭相机时必须记录 camera_mode、原因及验证结果。

## 当前阻塞项

### 1. 契约发布和兼容性

- [ ] 完成跨仓 data_postprocess session-workflow 兼容性测试。
- [ ] 保持 Parquet/JSONL 兼容策略和 legacy episode 行为有明确文档。

### 2. 标定、TF 和动作几何

- [ ] 增加 required transform-chain preflight 和 row/camera/calibration frame 一致性校验。
- [ ] 在可信 transform 输入可用后，补齐 FK end-effector pose/twist 或明确标记 unavailable。

### 3. 采集质量 gate

- [ ] 将 preflight、相机/机器人/Quest/CAN/E-stop 状态及过程中的健康变化写入 metadata/events/validation。
- [ ] 记录并校验 control frequency/jitter、send/feedback latency、camera FPS、drop/gap、duration 和 robot-state limits。
- [ ] 让 finalized episode 的 cleaning_ready 状态直接来自同一 validator；失败、未检查和 unavailable 不得标为通过。
- [ ] 要求 task、scene、robot、operator/anonymous operator、collection job 和 privacy 状态存在或显式 unavailable。

### 4. 可复现工作流

- [ ] 记录 code revision、resolved config、config hash、schema/capture profile、calibration hash 和 capability report。
- [ ] 增加 Canonical Raw、validator failure、下游 session conversion 和 legacy compatibility 的 smoke/CI 覆盖。

> 2026-07-24 新采 session `session_20260724_194504_bde610` 已经把第二阶段需要的事实写出来了；该批旧数据仍会因为相机 frame count skew 和 multicamera sync 被拦截。2026-07-26 已在数采侧实现三相机同步组写入修复，下一步需要真机复采验证。

## 已完成

- [x] 发布 checked-in schemas：`schema/canonical_raw_metadata.schema.json`、`schema/canonical_raw_rows.schema.json`、`schema/canonical_raw_calibration_snapshot.schema.json`、`schema/canonical_raw_manifest.schema.json`。
- [x] 固化 calibration/TF metadata 格式：episode metadata 记录 calibration version/path/SHA-256/status，fixture 同步输出 active rig snapshot、相机 intrinsics/distortion/image size/serial/frame ID、静态 transforms、来源和置信度。
- [x] 固化枚举：episode `recording_state`、`termination_reason`、timestamp unavailable reason 进入 `canonical_raw.contract` 和 validator。
- [x] 提供完整 no-hardware cleaning-ready fixture：`examples/cleaning_ready_fixture/session_cleaning_ready_fixture/episode_cleaning_ready_fixture`。
- [x] 提供生成命令并打印 session-workflow 调用：`python tools/generate_cleaning_ready_fixture.py examples/cleaning_ready_fixture --print-session-workflow`。
- [x] 增加 fixture/schema/validator smoke：`tests/test_cleaning_ready_fixture.py`。
- [x] 2026-07-24 新采 session 证明 stream frequency/drop/gap/latency/duration、action/state range/safety、calibration hash/frame chain、task/scene/robot/operator/privacy 事实已经可被下游读取。
- [x] 修复三相机同步组语义：同步器只写入 `<=40ms` 的完整三相机组，三路共享 `camera_stream_sequence_id`，`video_frame_index` 保持每路视频内部序号；新增前置相机领先/多帧和 active-state 竞态回归测试。

## 非当前阻塞项

- MCAP typed schema、完整 image/topic 解码和 MCAP sidecar recovery：仅在启用 MCAP 输入 profile 时实施；sampling 必须明确标为 sampled/unavailable。
- dashboard 展示细节、review hints 和 privacy sign-off 的派生流程：清洗侧只依赖结构化状态字段。
- Agent 候选标注和训练导出：由清洗侧和发布侧 TODO 管理，不写回 Canonical Raw。

## 完成定义

真实或 fixture session 能通过数采 validator，稳定生成 cleaning-ready episode，且清洗侧无需猜测 episode 边界、时间基、动作语义、相机状态或 lineage；缺失能力有稳定 reason code，所有关键字段可追溯到源文件、配置和标定版本。
