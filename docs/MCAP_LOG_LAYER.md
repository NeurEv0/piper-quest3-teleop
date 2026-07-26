# MCAP Raw Log Layer

The logging path is additive:

`hardware/Quest3 -> optional raw.mcap -> Canonical Raw -> training adapters`

Canonical Raw remains unchanged and is still written as JSONL, Parquet, and MP4. The operator-facing `start_vla_capture.sh` path enables MCAP by default. The internal Canonical launcher keeps MCAP behind its implementation flag so lower-level debugging can run without the sidecar.

The capture-side TODO for cleaning-ready raw inputs is tracked in
[`CAPTURE_SIDE_TODO.zh-CN.md`](CAPTURE_SIDE_TODO.zh-CN.md).

## Run

Use the operator-facing launcher when the rig is ready:

```bash
bash scripts/start_vla_capture.sh
```

That path delegates through the internal MCAP shadow wrapper, so each finalized
episode contains `raw.mcap`; `mcap_validation.json` is created by the
application after finalization. `run_bimanual_mcap_shadow.sh`,
`run_bimanual_canonical.sh`, and `ENABLE_MCAP` are implementation controls, not
operator start commands.

Validate a saved episode independently with `python scripts/validate_mcap_episode.py /path/to/episode`.

## Contract

The versioned topic contract is `schema/mcap_topic_contract_v1.json`. Structured messages use JSON Schema. Camera messages use a small `PIMG` envelope containing timestamps and geometry followed by JPEG bytes. MCAP records use host wall time for indexing while every payload retains host monotonic time and source time.

This first version is native MCAP rather than ROS messages because ROS 2 is not installed on the control computer. A future ROS 2 bridge can map these topics into `sensor_msgs`, `geometry_msgs`, `nav_msgs`, `tf2_msgs`, and `diagnostic_msgs` without replacing the recorder.

## Honest Sensor Coverage

Recorded now: three RGB cameras, Piper joint/gripper feedback, requested and sent actions, Quest3 controller poses/buttons, operator labels, control-loop and host/CAN health, metadata, and a calibration snapshot.

Not currently available: depth, LiDAR, IMU, odometry, and a validated tf tree. Point clouds are only derivable after depth and camera calibration exist. Typed end-effector state is planned; raw adapter state remains in `/robot/state` until FK output is added.

`calibration/rig_current.json` imports the existing ROS static TF configuration and three camera intrinsics. It is marked `usable_with_limitations`: camera transforms are solved, arm bases are operator-confirmed layout values, and tool offsets are nominal. Each transform preserves its own confidence and source provenance.

## Failure Isolation

MCAP writes happen inside the existing writer process. If the optional sidecar fails, it is abandoned and Canonical Raw still finalizes. The episode metadata records `mcap_log.status=error` and preserves the incomplete `raw.mcap.inprogress` for diagnosis.
