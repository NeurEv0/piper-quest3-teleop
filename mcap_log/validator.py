"""Structural and timing validation for Piper MCAP episode logs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .contract import CAMERA_NAMES, CAMERA_TOPIC_TEMPLATE, REQUIRED_TOPICS, ROW_TOPICS, SYSTEM_DIAGNOSTICS_TOPIC, TF_TOPIC
from .writer import decode_image_envelope


@dataclass(frozen=True)
class McapValidationReport:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def validate_mcap(path: Path, *, require_cameras: bool = True) -> McapValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}
    previous_time: dict[str, int] = {}
    camera_headers: dict[str, dict[str, object]] = {}
    calibration_status = "missing"
    tf_status = "missing"
    instruction_count = 0
    row_lineage: dict[str, list[tuple[str, int]]] = {
        ROW_TOPICS[kind]: [] for kind in ("control", "robot_feedback", "vr_input")
    }

    try:
        from mcap.reader import make_reader

        with Path(path).open("rb") as stream:
            reader = make_reader(stream, validate_crcs=True)
            for attachment in reader.iter_attachments():
                if attachment.name == "calibration_snapshot.json":
                    calibration = json.loads(attachment.data)
                    calibration_status = str(calibration.get("status", "unknown"))
                    tf_status = str(calibration.get("tf_status", calibration_status))
            for _schema, channel, message in reader.iter_messages(log_time_order=False):
                topic = channel.topic
                counts[topic] = counts.get(topic, 0) + 1
                if topic == "/annotation/instruction":
                    instruction_count += 1
                if message.log_time < previous_time.get(topic, 0):
                    errors.append(f"{topic} log_time is not monotonic")
                previous_time[topic] = message.log_time
                if channel.message_encoding == "json":
                    payload = json.loads(message.data)
                    for key in ("host_monotonic_ns", "host_wall_time_ns", "sequence_id"):
                        if key not in payload:
                            errors.append(f"{topic} message is missing {key}")
                    if topic in row_lineage:
                        for key in ("sample_id", "row_sequence_id", "control_sample_index", "source_timestamp_ns", "frame_id"):
                            if payload.get(key) is None:
                                errors.append(f"{topic} row message is missing {key}")
                        if payload.get("sample_id") is not None and payload.get("control_sample_index") is not None:
                            row_lineage[topic].append((str(payload["sample_id"]), int(payload["control_sample_index"])))
                elif channel.message_encoding == "piper.jpeg.v1":
                    header, jpeg = decode_image_envelope(message.data)
                    camera_headers[topic] = header
                    if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
                        errors.append(f"{topic} contains an invalid JPEG payload")
                else:
                    warnings.append(f"{topic} uses unknown encoding {channel.message_encoding}")
    except Exception as exc:
        errors.append(f"MCAP cannot be read: {exc}")

    for topic in REQUIRED_TOPICS:
        if counts.get(topic, 0) < 1:
            errors.append(f"required topic is missing: {topic}")
    if require_cameras:
        for name in CAMERA_NAMES:
            topic = CAMERA_TOPIC_TEMPLATE.format(camera_name=name)
            if counts.get(topic, 0) < 1:
                errors.append(f"required camera topic is missing: {topic}")
    if counts.get(SYSTEM_DIAGNOSTICS_TOPIC, 0) < 1:
        warnings.append("system diagnostics topic is missing")
    if instruction_count < 1:
        warnings.append("English VLA language instruction is missing (legacy episode)")
    if calibration_status != "valid":
        warnings.append(f"calibration snapshot status is {calibration_status}")
    if tf_status in {"valid", "usable_with_limitations"} and counts.get(TF_TOPIC, 0) < 1:
        errors.append("calibration is valid but /tf_static is missing")
    lineage_sets = {topic: set(values) for topic, values in row_lineage.items()}
    if lineage_sets and len({frozenset(values) for values in lineage_sets.values()}) != 1:
        errors.append("MCAP core row topics have inconsistent sample lineage")

    metrics: dict[str, object] = {
        "message_counts": counts,
        "camera_headers": camera_headers,
        "calibration_status": calibration_status,
        "tf_status": tf_status,
        "sample_lineage": {
            topic: [{"sample_id": sample_id, "control_sample_index": index} for sample_id, index in values]
            for topic, values in row_lineage.items()
        },
        "sample_lineage_consistent": bool(lineage_sets) and len({frozenset(values) for values in lineage_sets.values()}) == 1,
        "bytes": Path(path).stat().st_size if Path(path).is_file() else 0,
    }
    return McapValidationReport(not errors, tuple(dict.fromkeys(errors)), tuple(dict.fromkeys(warnings)), metrics)
