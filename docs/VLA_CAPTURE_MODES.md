# VLA Capture Modes and English Language Annotations

Canonical Raw + MCAP is the online recording source of truth. The archived `lerobot-record` launchers are stored outside the active repository at `/home/ylhp-e-ai/ZHITAI_1t/legacy_piper_lerobot_recording_20260722/`. Hardware adapters remain active dependencies; LeRobot datasets should be produced later by an offline exporter.

## Camera Modes

`off` disconnects all three camera workers and releases their USB devices. An Episode may still record robot, Quest3, command, diagnostic, tf, and language streams. It is valid as a control-only log but does not contain visual observations.

`mosaic` opens all three cameras. Canonical Raw and MCAP retain the three independent source streams. Quest3 receives a human preview with the front view above the two wrist views; the composite preview is not used as a training observation.

Mode changes are accepted only while the recorder is `IDLE`. The selected mode is frozen in Episode metadata and determines whether validation requires three camera streams.

## English VLA Contract

There is no universal action-primitive vocabulary shared by every VLA implementation. This project therefore preserves the broadly compatible free-text task field and versions its structured extension as `piper.vla.language.v1`.

Episode metadata contains both `task` and `language_instruction` with identical English text. MCAP also writes the instruction to `/annotation/instruction`.

Timestamped operator annotations are written to `language_actions.jsonl`, `language_actions.parquet`, and `/annotation/language_action`. The primitive vocabulary is:

`approach`, `grasp`, `lift`, `transport`, `place`, `release`, `push`, `pull`, `rotate`, `align`, `hold`, `retract`, `reset`.

Each annotation includes the English `language_action`, `primitive`, `arm`, `object`, `target`, source, and host timestamps. Chinese text is rejected from the English training fields. The machine-readable schema is `schema/vla_language_annotation_v1.json`.
