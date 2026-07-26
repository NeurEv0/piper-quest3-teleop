# Canonical Raw Recording

`piper_canonical_raw_v1` is the hardware-facing source-of-truth format for the
dual-Piper Quest3 rig. It does not use `LeRobotDataset` and does not perform
training-time alignment or normalization.

The cleaning-ready capture TODO is tracked in
[`CAPTURE_SIDE_TODO.zh-CN.md`](CAPTURE_SIDE_TODO.zh-CN.md).
The capture-side implementation layers are defined in
[`CAPTURE_SIDE_LAYERS.zh-CN.md`](CAPTURE_SIDE_LAYERS.zh-CN.md).

## Operator workflow

1. Connect both arms, three configured Orbbec cameras, the emergency stop, and
   Quest3.
2. Run `bash scripts/start_vla_capture.sh` and select the task, or pass
   `--task`, `--operator`, and `--scene` explicitly for a scripted session.
3. Open `http://localhost:8020`, verify the resolved operator/task context, and
   wait until all blocking checks pass.
4. Enter Quest VR at `https://localhost:8012?ws=wss://localhost:8012`.
5. Start the episode from the dashboard or headset gesture, then end it as
   success, failure, or abort. The dashboard reports automatic validation after
   finalization.
6. When the recorder is idle, stop the capture service with
   `bash scripts/stop_vla_capture.sh`.

The active launch chain is `start_vla_capture.sh -> run_bimanual_mcap_shadow.sh
-> run_bimanual_canonical.sh -> record_bimanual_canonical.py`. Only
`start_vla_capture.sh` and `stop_vla_capture.sh` are operator-facing; the middle
files are implementation details for debugging and tests.

With the headset on, hold left Y and right B together for one second to start.
After releasing both buttons, hold right B for one second to finish as success,
or left Y for one second to finish as failure. The front video shows `READY`,
`REC mm:ss`, or `FINALIZING`; these labels are only rendered into the headset
display buffer and are not burned into the recorded camera video.

## Storage contract

Each session owns immutable finalized episode directories. Active episodes use
the `.inprogress` suffix so an interrupted write cannot look training-ready.

```text
session_YYYYMMDD_HHMMSS_xxxxxx/
  session.json
  episode_YYYYMMDD_HHMMSS_xxxxxx/
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

Raw streams retain their independent host monotonic timestamps. Alignment is a
future derived-data operation. `control.parquet` stores both requested and
actually returned/sent actions. `robot_feedback.parquet` stores observations,
and `vr_input.parquet` stores both controller poses and complete button states.

## Failure behavior

- A failed static preflight exits before robot connection.
- The emergency-stop process alone is insufficient: its `estop.ready` device
  marker must exist. `--allow-no-estop` is an explicit unsafe bench-only override.
- Camera green-screen, stale Quest events, stale robot feedback, or a dead writer
  blocks episode start.
- The same health checks continue during recording. Any blocking transition is
  written to `events.jsonl`, retained in metadata, and makes final validation
  fail even if the process later recovers.
- Camera queue pressure drops camera frames rather than blocking arm control and
  records the drop count in metadata.
- Normal completion creates Parquet/video files, checksums them, writes the
  manifest, and atomically removes the `.inprogress` suffix.
- Shutdown during an episode preserves the `.inprogress` directory with an
  aborted reason. It is never considered training-ready.

## No-hardware smoke test

Run the storage round-trip test in the `lerobot` environment:

```bash
python -m pytest -q tests/test_canonical_raw.py
```

The full application also supports `--mock-hardware --mock-vr
--allow-no-cameras` for control-loop testing, but those episodes are explicitly
camera-incomplete and are not eligible for training.
