# 数采侧清洗可用输入 TODO

Updated: 2026-07-24 (Asia/Tokyo)

本文档是 Piper Quest3 VR 遥操作数采侧为了满足
`data_postprocess` 清洗、标准化、验证和发布要求而维护的正式执行清单。

相关工作区：

- 数采实现工作区：`piper-quest3-teleop-feature-lerobot`
- 原始数采存储区：`/home/ylhp-e-ai/ZHITAI_1t/piper_canonical_raw`
- 清洗与标准化工作区：`data_postprocess`
- 清洗侧主 TODO：`data_postprocess/docs/TODO.md`

## 0. 目标与边界

目标：

- 让每个真实采集 session 都能稳定进入 `data_postprocess session-workflow`。
- 让清洗侧可以确定性重建时间线、动作语义、相机同步、raw lineage、episode 边界和质量报告。
- 所有无法提供的模态或能力必须显式记录为 unavailable / intentionally disabled / not checked，不允许空指标伪装成通过。

边界：

- Canonical Raw 是数采侧 source of truth，不直接承担训练时重采样、滤波、切分策略选择或 policy action 派生。
- 后处理产生的 canonical dataset、review 决策、Agent 候选标注和训练 gate 不写回原始数采目录。
- MCAP 是可选 shadow log 层；启用后不得影响 Canonical Raw 正常落盘。

## 1. 当前契约基线

当前已经存在并开始落地的契约：

- Storage schema：`piper_canonical_raw_v1`
- Capture contract：`piper_capture_cleaning_ready_v1`
- Action semantics：`piper_action_semantics_v1`
- 主时间基：`host_monotonic_ns`
- 墙钟时间：`host_wall_time_ns`
- 核心同步流：`control`、`robot_feedback`、`vr_input`
- 核心行字段：`sample_id`、`row_sequence_id`、`host_monotonic_ns`、`host_wall_time_ns`、`source_timestamp_ns`

当前每个 finalized episode 应至少包含：

```text
metadata.json
control.parquet
robot_feedback.parquet
vr_input.parquet
camera_timestamps.parquet
camera_cam_front.mp4
camera_cam_left_wrist.mp4
camera_cam_right_wrist.mp4
events.jsonl
manifest.json
validation.json
```

当相机被显式关闭时，视频文件可以不存在，但 `camera_mode`、原因、核心控制流和验证结果必须完整。

## 2. 清洗侧阻塞映射

| 清洗侧 TODO 项 | 数采侧需要交付 | 当前状态 |
| --- | --- | --- |
| P3.1 MCAP configured stream full decode | 完整 topic 契约、sample 同步字段、可解码 payload、decode coverage 报告 | 未完成 |
| P3.1 continuous-log episode slicing | 连续 session 事件流、start/stop/reset/task-change/abort/incomplete 边界事件 | 未完成 |
| P3.2 fixed-rate timeline | 每个源字段的 source/receive/send/executed timestamp 和 sample_id 对齐键 | 部分完成 |
| P3.2 full clock provenance | sensor capture、receive、command-issued、executed、ingestion、annotation timestamp | 部分完成 |
| P3.2 action semantics | teleop intent、controller command、robot executed、policy action 输入语义分离 | 部分完成 |
| P3.2 tf/calibration application | 标定版本、tf tree、camera intrinsics/extrinsics、frame id 和可信度 | 未完成 |
| P3.3 robot/frequency/duration checks | 数采侧频率、延迟、drop、state range、episode duration 指标 | 部分完成 |
| P3.4 synchronized review media | 多相机 frame 时间线、动作/状态/event 轨道一致的 source timestamps | 部分完成 |
| P6 release reproducibility | capture profile、schema version、code commit、config hash、raw manifest lineage | 部分完成 |

## C0 - Cleaning-Ready Contract Baseline

Priority: highest. This phase establishes the minimum deterministic input
contract consumed by `data_postprocess`.

- [x] Add versioned capture contract constants in `canonical_raw/contract.py`.
- [x] Store `capture_contract_version`, `timebase`, `action_semantics_version`, and `action_semantics` in episode metadata.
- [x] Enrich core rows with `row_sequence_id`, `host_wall_time_ns`, `source_timestamp_ns`, and fallback `sample_id`.
- [x] Write the same `sample_id` across `control`, `robot_feedback`, and `vr_input` for real recording loop samples.
- [x] Emit structured `action_requested`, `action_sent`, `observation`, and `event_status` fields while preserving legacy JSON strings.
- [x] Track `stream_sequence_counts` and per-camera synchronization metrics in metadata and manifest.
- [x] Add cleaning-ready validator checks for timebase, action semantics, row fields, row sequence ordering, and sample coverage.
- [x] Update `data_postprocess` session adapter to prefer structured fields and preserve source provenance.
- [ ] Publish checked-in JSON Schemas for cleaning-ready `metadata.json`, row tables, `camera_timestamps.parquet`, and `manifest.json`.
- [ ] Add a short migration note for legacy episodes that predate `piper_capture_cleaning_ready_v1`.
- [x] Link this TODO from `CANONICAL_RAW_RECORDING.md` and `MCAP_LOG_LAYER.md`.

Acceptance criteria:

- A no-hardware fixture validates with `capture_contract_version=piper_capture_cleaning_ready_v1`.
- `sample_sync.coverage_rate == 1.0` for complete control episodes.
- Legacy episodes remain readable, but missing cleaning-ready fields are warnings unless the metadata declares the cleaning-ready contract.

## C1 - Session And Episode Boundary Completeness

Priority: highest. This phase removes ambiguity between pre-segmented episode
directories and continuous collection sessions.

- [x] Add an append-only session event stream, for example `session_events.jsonl`, with monotonically increasing host timestamps.
- [x] Record operator start, operator stop, success, failure, abort, reset, task change, hardware block, camera mode change, dashboard command, and process shutdown events.
- [x] Record `episode_start_host_monotonic_ns`, `episode_end_host_monotonic_ns`, `duration_s`, `termination_reason`, `task_success`, and `slicing_rule` in every episode metadata file.
- [x] Preserve incomplete or interrupted episodes with explicit `recording_state=incomplete` or `recording_state=aborted`.
- [x] Add a recovery/inspection command for `.inprogress` directories that never marks them training-ready automatically.
- [x] Add fixtures for clean completion, abort, process interruption, task change, and time-gap slicing.

Session events use `piper_session_event_v1` with required `event_type`, `episode_id`,
`reason`, `source`, and `payload` fields. Inspect preserved interrupted episodes with:

```bash
python tools/inspect_inprogress.py /path/to/raw --report /path/to/inprogress_diagnostic.json
```

Generate the deterministic no-hardware boundary fixtures with:

```bash
python tools/generate_c1_fixtures.py /tmp/piper_c1_fixtures
```

Acceptance criteria:

- Episode boundaries are deterministic, overlap-free, and reconstructable from session-level events.
- Cleaning side can test reset, gap, task-change, aborted, and incomplete episode slicing without relying on private local data.

## C2 - Clock Provenance And Stream Synchronization

Priority: highest. This phase lets the cleaner build fixed-rate timelines and
measure alignment error instead of guessing from row order.

- [x] Store core row `host_monotonic_ns`, `host_wall_time_ns`, and `source_timestamp_ns`.
- [x] Store paired sample IDs for `control`, `robot_feedback`, and `vr_input`.
- [x] Add `control_sample_index` to all writer paths, including MCAP shadow rows and generated fixtures.
- [x] Add command lifecycle timestamps: request generated, send start, send end, and send result received.
- [x] Add robot feedback lifecycle timestamps: hardware/source timestamp when available, host receive timestamp, and post-read enqueue timestamp.
- [x] Add Quest3 lifecycle timestamps: controller event source timestamp, host receive timestamp, stale age, and last event sequence.
- [x] Add camera lifecycle timestamps: sensor/source timestamp when available, host receive timestamp, enqueue timestamp, write timestamp, and per-camera sequence ID.
- [x] Record per-stream measured frequency, timestamp monotonicity, max gap, median/P95 lag, and drop count in episode validation.
- [x] Add thresholds for sample coverage, camera frame skew, stale Quest input, stale robot feedback, and control-loop frequency.

C2 validator checks use stable reason codes and explicit `pass` / `fail` /
`unavailable` states. Missing source capability or missing lifecycle samples are
reported as unavailable and are never represented as a passing check. The
no-hardware fault suite covers timestamp regression, lifecycle reversal,
missing unavailable reasons, large gaps, drops, duplicate sample IDs, stale
Quest/feedback, camera write latency, and multicamera skew.

Acceptance criteria:

- Every canonical step can trace each aligned field back to source timestamp and capture stream.
- Validator errors when required timestamps are missing from a cleaning-ready episode.
- Reports include per-field or per-stream lag distributions, not only aggregate row counts.

## C3 - Action, State, Unit, And Frame Semantics

Priority: high. This phase makes training targets auditable and prevents
`teleop.intent_t`, `controller.command_t`, and `robot.executed_t` from being mixed.

- [x] Store `teleop.intent_t`, `controller.command_t`, `robot.executed_t`, and `policy.action_t` descriptions in action semantics metadata.
- [x] Store structured requested action, sent action, robot observation, and VR event status in core rows.
- [x] Publish an `action_space` contract with dimension names, units, frame IDs, source field paths, command limits, and conversion version.
- [x] Freeze collection profiles for `record_action_from_follower`, `teleop_joint_alpha`, `teleop_gripper_alpha`, speed limits, and safety clamp behavior.
- [x] Emit typed gripper target, sent gripper command, and measured gripper state separately.
- [x] Emit typed joint position, joint velocity, joint command, command error, and safety state with SI units.
- [ ] Emit end-effector pose/twist from FK with frame ID and FK version once transform inputs are trusted.
- [x] Preserve IK target, IK result, smoothing delta, clamp status, and IK failure reason per control sample.
- [x] Add action/state range checks against robot configuration and hardware limits.

C3 is frozen as `piper_bimanual_joint_position_v1`: 14 ordered SI dimensions
(left joints 1-6, left gripper, right joints 1-6, right gripper). Position-only
adapter capabilities such as joint velocity, hardware safety result, and robot-side
clamp status are emitted as `unavailable` with stable reason codes; they are not
reported as passed. C3 verification is offline-only and does not claim hardware
limit validation.

Run the standalone validator with:

```bash
python tools/validate_c3_episode.py /path/to/finalized_episode --report /tmp/c3_validation.json
```

Acceptance criteria:

- Downstream `policy.action_t` can be derived reproducibly without reading ambiguous legacy JSON strings.
- Gripper labels are correct for the selected collection profile.
- Unit, frame, source stream, and transformation version are traceable for every action/state dimension.

## C4 - Calibration, TF, And Camera Geometry

Priority: high. This phase unblocks transform validation, camera geometry checks,
and frame conversion in the cleaning pipeline.

- [ ] Store `calibration_version`, calibration file path, calibration SHA-256, and calibration status in session and episode metadata.
- [ ] Version `calibration/rig_current.json` as the active rig snapshot and record its limitations in capture metadata.
- [ ] Emit camera intrinsics, distortion model, image size, serial number, and frame ID for each camera.
- [ ] Emit static transforms for world/base, left arm base, right arm base, wrist cameras, front camera, gripper/tool frames, and Quest/controller frames.
- [ ] Store transform source, confidence, timestamp, and validation status per transform.
- [ ] Add an online preflight check for required transform chains.
- [ ] Add a validator check that frame IDs in row payloads, camera timestamps, and calibration agree.
- [ ] Add one calibrated fixture that exercises successful and missing-transform paths.

Acceptance criteria:

- Missing required transform chains block cleaning-ready recording or fail episode validation.
- Cleaning side can apply frame conversions with explicit calibration provenance.
- Camera streams have enough geometry metadata for multi-camera review and future depth/point-cloud extensions.

## C5 - MCAP Shadow Log Completeness

Priority: medium-high. This phase makes MCAP useful as a raw audit and recovery
format without replacing Canonical Raw.

- [x] Keep MCAP optional and isolated from Canonical Raw finalization.
- [x] Document MCAP topic profile and honest sensor coverage.
- [x] Include `sample_id`, `row_sequence_id`, `control_sample_index`, `source_timestamp_ns`, and frame IDs in all MCAP row topics.
- [ ] Add typed JSON Schemas for command, robot state, VR state, event, language annotation, diagnostics, capabilities, calibration, and tf status payloads.
- [ ] Add image decode validation for all configured camera topics and distinguish full decode from sampled inspection.
- [ ] Record MCAP channel counts, message counts, decode coverage, first/last timestamps, and schema versions in `mcap_validation.json`.
- [ ] Add data_postprocess or sidecar reader tests that fully decode configured MCAP image, robot state, command, teleop, diagnostics, event, transform, and calibration streams.
- [ ] Keep MCAP sidecar failure isolated and preserve `raw.mcap.inprogress` for diagnosis.

Acceptance criteria:

- Sampling is never reported as full extraction.
- All required MCAP topics are either fully decoded or explicitly unavailable with reason.
- Canonical Raw and MCAP agree on episode ID, sample IDs, core stream counts, and timestamp ranges.

## C6 - Capture Quality Gate And Operator Metadata

Priority: medium-high. This phase turns hardware health into deterministic
metadata and validation reports.

- [ ] Persist static preflight results, camera health, robot health, Quest freshness, CAN status, E-stop status, and dashboard status into session metadata.
- [ ] Persist during-recording health transitions into `events.jsonl` and episode validation.
- [ ] Add robot-state range, velocity, acceleration, command error, safety clamp, feedback timeout, and watchdog checks.
- [ ] Add control-loop frequency, jitter, send latency, feedback latency, and camera FPS distributions.
- [ ] Add episode duration thresholds and warning/error levels.
- [ ] Add operator, task, scene, object set, capture job, anonymized operator ID, privacy status, and collection notes to required dashboard fields or explicit unavailable fields.
- [ ] Add a `cleaning_ready` dashboard indicator that is derived from the same validator used after finalization.

Acceptance criteria:

- An operator cannot accidentally mark an unhealthy cleaning-ready episode as valid.
- Every release-facing quality report has real metrics or explicit unavailable reasons.
- Data cleaning can group by task, scene, robot, operator, collection job, and quality status.

## C7 - Workflow, Fixtures, And Reproducibility

Priority: medium. This phase makes capture-side changes repeatable and safe to
use from the cleaning workflow.

- [ ] Add a one-command no-hardware cleaning-ready fixture generator that writes a complete session directory.
- [ ] Add a small committed or generated real-session-style regression fixture that does not depend on local unversioned data.
- [ ] Add a capture-side command that prints the exact `data_postprocess session-workflow` invocation for a finalized session.
- [ ] Add CI or local smoke coverage for Canonical Raw, MCAP, validator failure paths, and downstream session conversion.
- [ ] Record code revision or unavailable reason, conda environment name, dependency capability report, launcher args, and resolved capture defaults in session metadata.
- [ ] Add config hash and calibration hash to session metadata and raw manifest lineage.
- [ ] Add compatibility tests for legacy episodes and cleaning-ready episodes.

Acceptance criteria:

- A new developer can produce and clean a fixture without hardware access.
- A real session can be reproduced by manifest, config, schema version, code revision, and calibration hash.
- Capture-side contract changes fail tests until `data_postprocess` adapters are updated.

## C8 - Privacy And Review Readiness

Priority: medium. This phase makes privacy and human review status explicit at
capture time while leaving review decisions as derived artifacts.

- [ ] Record per-camera privacy status: `not_scanned`, `candidate_found`, `redacted`, `review_required`, `approved`, or `not_applicable`.
- [ ] Record whether operator-entered metadata is anonymized or raw.
- [ ] Preserve raw video without burned-in dashboard overlays or headset status text.
- [ ] Add optional operator notes and reviewer hints as separate metadata fields, not canonical labels.
- [ ] Add a privacy sign-off placeholder that cleaning/release gates can treat as unavailable until reviewed.

Acceptance criteria:

- Privacy status is never silently assumed.
- Capture metadata can feed release gates without mutating raw video or canonical fields.

## 3. Verification Matrix

Run after changes that touch Canonical Raw:

```bash
conda run -n lerobot python -m pytest -q tests/test_canonical_raw.py
```

Run after changes that touch MCAP:

```bash
conda run -n lerobot python -m pytest -q tests/test_mcap_log.py
```

Run after changes that affect both capture and cleaning adapters:

```bash
conda run -n lerobot python -m pytest -q tests/test_canonical_raw.py tests/test_mcap_log.py
cd ../data_postprocess
env PYTHONPATH=src python -m unittest tests.test_platform_adapters tests.test_session_workflow
```

Run after schema or report changes:

```bash
python -m py_compile canonical_raw/contract.py canonical_raw/recorder.py canonical_raw/validator.py scripts/record_bimanual_canonical.py
```

Hardware-facing changes also require an operator-approved run card before moving
arms. No mock test can replace E-stop, CAN mapping, camera identity, Quest
freshness, and workspace safety confirmation.

## 4. Recommended Execution Order

1. Finish C0 schema publication and documentation links.
2. Implement C1 session event stream and interrupted/incomplete fixtures.
3. Complete C2 timestamp lifecycle fields and validator thresholds.
4. Finish C3 action-space and gripper/state semantics.
5. Add C4 calibration/tf provenance and required-chain validation.
6. Complete C5 MCAP full decode coverage.
7. Add C6 dashboard cleaning-ready gate and operator metadata requirements.
8. Add C7 fixture/reproducibility automation.
9. Wire C8 privacy status into release-facing metadata.

## 5. Definition Of Done

A checkbox may be marked complete only when:

- The implementation is reachable from a documented launcher, CLI, dashboard action, validator, or workflow.
- Success, failure, and unavailable-capability behavior are covered by focused tests or a documented hardware run card.
- Episode metadata, manifest, validation output, and downstream cleaning adapter all agree on the field semantics.
- Legacy compatibility is either preserved or explicitly documented as unsupported with migration guidance.
- No Agent proposal, human review decision, or training export mutates Canonical Raw source data.
