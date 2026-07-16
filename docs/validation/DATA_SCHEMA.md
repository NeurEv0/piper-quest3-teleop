# Canonical Episode Schema

Version: `piper_canonical_v1`

The machine-readable contract is `schema/canonical_episode.schema.json`. A raw
episode is one JSON object with `metadata` and a non-empty `frames` array. This
JSON representation is the contract fixture; production storage may use Parquet,
video, and sidecar metadata as long as it round-trips without semantic loss.

## Metadata

Required identifiers: episode, operator, task, schema, calibration, robot URDF,
teleoperation commit, and control commit. Camera configuration, object initial
pose, target pose, start/end host monotonic timestamps, stage outcome labels,
task outcome, and failure reason are mandatory.

## Per-frame raw fields

- `host_monotonic_ns`, `phase`, and `source_index`.
- Quest pose, buttons/squeeze, device time, and host receive time.
- TCP target with generation time and `T_base_tcp` pose.
- IK output `q_command`, send time, status, and safety-filter decision.
- Robot `q_actual`, `dq_actual`, feedback time, receive time, and TCP actual.
- Gripper command/actual, controller mode, and safety state.
- Every camera sample: source index, device time, receive time, optical frame,
  image reference, decode status, and dimensions.

Raw camera bytes live outside JSON and are addressed by `image_ref`. Derived
alignment fields must not overwrite any raw timestamp or value.
