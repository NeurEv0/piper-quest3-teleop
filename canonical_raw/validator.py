"""Validate a finalized Canonical Raw episode directory."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from canonical_raw.contract import (
    C2_VALIDATION_THRESHOLDS,
    CAPTURE_CONTRACT_VERSION,
    CORE_ROW_FIELDS,
    LIFECYCLE_ROW_FIELDS,
    SAMPLE_SYNC_STREAMS,
)


REQUIRED_TABLES = ("control.parquet", "robot_feedback.parquet", "vr_input.parquet")


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    metrics: dict[str, object]
    reason_codes: tuple[str, ...]
    checks: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _strictly_increasing(values: list[int]) -> bool:
    return all(right > left for left, right in zip(values, values[1:]))


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    lower = math.floor(rank)
    upper = math.ceil(rank)
    value = ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)
    return round(value, 6)


def _distribution(values: list[float], *, unit: str) -> dict[str, object]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "status": "available" if finite else "unavailable",
        "unavailable_reason": None if finite else "no_samples",
        "unit": unit,
        "sample_count": len(finite),
        "median": _percentile(finite, 0.50),
        "p95": _percentile(finite, 0.95),
        "max": round(max(finite), 6) if finite else None,
    }


def _latency_distribution(rows: list[dict[str, object]], start: str, end: str) -> dict[str, object]:
    values = [
        (int(row[end]) - int(row[start])) / 1e6
        for row in rows
        if isinstance(row.get(start), int) and isinstance(row.get(end), int) and int(row[end]) >= int(row[start])
    ]
    return _distribution(values, unit="ms")


def _stream_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    times = [int(row["host_monotonic_ns"]) for row in rows if isinstance(row.get("host_monotonic_ns"), int)]
    gaps = [(right - left) / 1e6 for left, right in zip(times, times[1:])]
    duration_s = (times[-1] - times[0]) / 1e9 if len(times) > 1 else 0.0
    sequences = [int(row["row_sequence_id"]) for row in rows if isinstance(row.get("row_sequence_id"), int)]
    sequence_drops = sum(max(0, right - left - 1) for left, right in zip(sequences, sequences[1:]))
    sample_indices = [int(row["control_sample_index"]) for row in rows if isinstance(row.get("control_sample_index"), int)]
    sample_drops = sum(max(0, right - left - 1) for left, right in zip(sample_indices, sample_indices[1:]))
    median_gap = _percentile(gaps, 0.5)
    jitter = [abs(gap - median_gap) for gap in gaps] if median_gap is not None else []
    return {
        "sample_count": len(rows),
        "timestamp_monotonic": _strictly_increasing(times) and len(times) == len(rows),
        "measured_frequency_hz": round((len(times) - 1) / duration_s, 6) if duration_s > 0 else None,
        "gap_ms": _distribution(gaps, unit="ms"),
        "jitter_ms": _distribution(jitter, unit="ms"),
        "drop_count": max(sequence_drops, sample_drops),
    }


def _reason_code(message: str) -> str:
    patterns = (
        ("timestamps are not strictly increasing", "timestamp.regression"),
        ("lifecycle timestamps are reversed", "lifecycle.order_reversed"),
        ("lacks source timestamp and unavailable reason", "source_timestamp.reason_missing"),
        ("duplicate sample_id", "sample_id.duplicate"),
        ("sample_id coverage", "sample_coverage.incomplete"),
        ("missing from paired streams", "sample_coverage.incomplete"),
        ("row_sequence_id is not strictly increasing", "row_sequence.invalid"),
    )
    for fragment, code in patterns:
        if fragment in message:
            return code
    normalized = "".join(character.lower() if character.isalnum() else "." for character in message)
    return ".".join(part for part in normalized.split(".") if part)[:96] or "validation.error"


def _add_check(
    checks: list[dict[str, object]], errors: list[str], *, name: str, value: float | None,
    threshold: object, passed: bool | None, reason_code: str, detail: str,
) -> None:
    status = "unavailable" if passed is None else "pass" if passed else "fail"
    checks.append({"name": name, "status": status, "value": value, "threshold": threshold, "reason_code": reason_code, "detail": detail})
    if passed is False:
        errors.append(f"[{reason_code}] {detail}")


def validate_episode(path: Path, *, require_cameras: bool = True) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, object] = {}
    checks: list[dict[str, object]] = []
    metrics["thresholds"] = dict(C2_VALIDATION_THRESHOLDS)

    metadata_path = path / "metadata.json"
    if not metadata_path.is_file():
        errors.append("metadata.json is missing")
        metadata: dict[str, object] = {}
    else:
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"metadata.json is unreadable: {exc}")
            metadata = {}

    for key in ("episode_id", "operator_id", "task_id", "schema_version", "start_host_monotonic_ns"):
        if not metadata.get(key):
            errors.append(f"metadata.{key} is missing")
    if not metadata.get("language_instruction"):
        warnings.append("metadata.language_instruction is missing (legacy episode)")
    if metadata.get("camera_mode") not in {None, "off", "mosaic"}:
        errors.append(f"metadata.camera_mode is invalid: {metadata.get('camera_mode')}")
    runtime_failures = metadata.get("runtime_failures", [])
    if runtime_failures:
        errors.append(f"runtime health failures occurred: {runtime_failures}")
    if int(metadata.get("dropped_camera_frames", 0) or 0) > 0:
        warnings.append(f"camera writer queue dropped {metadata['dropped_camera_frames']} frames")
    cleaning_ready = metadata.get("capture_contract_version") == CAPTURE_CONTRACT_VERSION
    metrics["capture_contract_version"] = metadata.get("capture_contract_version")
    if cleaning_ready:
        _validate_cleaning_ready_metadata(metadata, errors)

    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - deployment dependency
        errors.append(f"pyarrow is unavailable: {exc}")
        pq = None

    if pq is not None:
        tables: dict[str, object] = {}
        for filename in REQUIRED_TABLES:
            table_path = path / filename
            if not table_path.is_file():
                errors.append(f"{filename} is missing")
                continue
            try:
                table = pq.read_table(table_path)
                tables[Path(filename).stem] = table
                metrics[f"{filename}.rows"] = table.num_rows
                if table.num_rows == 0:
                    errors.append(f"{filename} is empty")
                    continue
                _validate_core_row_contract(
                    table,
                    filename,
                    cleaning_ready=cleaning_ready,
                    require_sample_id=True,
                    errors=errors,
                    warnings=warnings,
                    metrics=metrics,
                )
                _validate_lifecycle_contract(table, Path(filename).stem, cleaning_ready, errors)
                rows = table.to_pylist()
                metrics[f"streams.{Path(filename).stem}"] = _stream_metrics(rows)
                times = table.column("host_monotonic_ns").to_pylist()
                if not _strictly_increasing([int(value) for value in times]):
                    errors.append(f"{filename} timestamps are not strictly increasing")
            except Exception as exc:
                errors.append(f"{filename} cannot be read: {exc}")

        _validate_sample_sync(tables, cleaning_ready, errors, warnings, metrics)
        _validate_c2_core_thresholds(tables, metadata, checks, errors, metrics)

        camera_table = path / "camera_timestamps.parquet"
        if require_cameras and not camera_table.is_file():
            errors.append("camera_timestamps.parquet is missing")
        elif camera_table.is_file():
            try:
                table = pq.read_table(camera_table)
                metrics["camera_timestamps.parquet.rows"] = table.num_rows
                _validate_core_row_contract(
                    table,
                    "camera_timestamps.parquet",
                    cleaning_ready=cleaning_ready,
                    require_sample_id=False,
                    errors=errors,
                    warnings=warnings,
                    metrics=metrics,
                )
                _validate_lifecycle_contract(table, "camera", cleaning_ready, errors)
                names = sorted(set(table.column("camera_name").to_pylist()))
                metrics["camera_names"] = names
                if require_cameras and len(names) < 3:
                    errors.append(f"expected 3 camera streams, found {len(names)}")
                camera_names = table.column("camera_name").to_pylist()
                camera_times = [int(value) for value in table.column("host_monotonic_ns").to_pylist()]
                camera_rows = table.to_pylist()
                for name in names:
                    stream_rows = [row for row in camera_rows if row.get("camera_name") == name]
                    metrics[f"streams.camera.{name}"] = _stream_metrics(stream_rows)
                    times = [stamp for stream, stamp in zip(camera_names, camera_times) if stream == name]
                    if not _strictly_increasing(times):
                        errors.append(f"camera {name} timestamps are not strictly increasing")
                    if len(times) >= 2:
                        duration_s = (times[-1] - times[0]) / 1e9
                        fps = (len(times) - 1) / duration_s if duration_s > 0 else 0.0
                        metrics[f"camera.{name}.fps"] = fps
                        if require_cameras and fps < 20.0:
                            errors.append(f"camera {name} average rate is {fps:.1f} FPS, below 20 FPS")
                    metrics[f"camera.{name}.frames_from_timestamps"] = len(times)
                if names:
                    frame_counts = [
                        int(metrics[f"camera.{name}.frames_from_timestamps"]) for name in names
                    ]
                    frame_skew = max(frame_counts) - min(frame_counts)
                    metrics["camera_frame_count_skew"] = frame_skew
                    if require_cameras and frame_skew:
                        warnings.append(f"camera frame count skew is {frame_skew} frames")
                dropped = sum(not bool(value) for value in table.column("decoded").to_pylist())
                metrics["camera_undecoded"] = dropped
                if dropped:
                    warnings.append(f"{dropped} camera samples were marked undecoded")
                _validate_c2_camera_thresholds(camera_rows, require_cameras, checks, errors, metrics)
            except Exception as exc:
                errors.append(f"camera_timestamps.parquet cannot be read: {exc}")

        language_actions = path / "language_actions.parquet"
        if language_actions.is_file():
            try:
                from canonical_raw.vla_annotations import ARMS, PRIMITIVES

                table = pq.read_table(language_actions)
                metrics["language_actions.parquet.rows"] = table.num_rows
                for index, row in enumerate(table.to_pylist()):
                    if row.get("primitive") not in PRIMITIVES:
                        errors.append(f"language action {index} has invalid primitive")
                    if row.get("arm") not in ARMS:
                        errors.append(f"language action {index} has invalid arm")
                    if not row.get("language_action"):
                        errors.append(f"language action {index} is missing English text")
            except Exception as exc:
                errors.append(f"language_actions.parquet cannot be read: {exc}")

    if require_cameras:
        videos = sorted(path.glob("camera_*.mp4"))
        metrics["video_files"] = [item.name for item in videos]
        if len(videos) < 3:
            errors.append(f"expected 3 video files, found {len(videos)}")
        for video in videos:
            if video.stat().st_size < 1024:
                errors.append(f"{video.name} is too small to be a valid recording")
                continue
            try:
                import cv2

                capture = cv2.VideoCapture(str(video))
                ok, frame = capture.read()
                frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                capture.release()
                metrics[f"{video.name}.frames"] = frame_count
                timestamp_key = (
                    f"camera.{video.name.removeprefix('camera_').removesuffix('.mp4')}.frames_from_timestamps"
                )
                timestamp_count = metrics.get(timestamp_key)
                if isinstance(timestamp_count, int) and frame_count != timestamp_count:
                    errors.append(
                        f"{video.name} frame count {frame_count} does not match timestamp rows {timestamp_count}"
                    )
                if not ok or frame is None or frame_count < 1:
                    errors.append(f"{video.name} cannot be decoded")
            except Exception as exc:
                errors.append(f"{video.name} decode check failed: {exc}")

    reason_codes = tuple(dict.fromkeys(
        [str(check["reason_code"]) for check in checks if check["status"] in {"fail", "unavailable"}]
        + [_reason_code(message) for message in errors]
    ))
    return ValidationReport(not errors, tuple(errors), tuple(warnings), metrics, reason_codes, tuple(checks))


def _validate_cleaning_ready_metadata(metadata: dict[str, object], errors: list[str]) -> None:
    for key in (
        "episode_start_host_monotonic_ns",
        "episode_end_host_monotonic_ns",
        "duration_s",
        "termination_reason",
        "slicing_rule",
    ):
        if metadata.get(key) is None:
            errors.append(f"metadata.{key} is missing for cleaning-ready capture")
    start_ns = metadata.get("episode_start_host_monotonic_ns")
    end_ns = metadata.get("episode_end_host_monotonic_ns")
    if start_ns is not None and end_ns is not None and int(end_ns) < int(start_ns):
        errors.append("metadata episode boundary timestamps are reversed")
    timebase = metadata.get("timebase")
    if not isinstance(timebase, dict) or timebase.get("primary") != "host_monotonic_ns":
        errors.append("metadata.timebase.primary must be host_monotonic_ns for cleaning-ready capture")
    semantics = metadata.get("action_semantics")
    if not isinstance(semantics, dict):
        errors.append("metadata.action_semantics is missing")
        return
    for key in ("teleop.intent_t", "controller.command_t", "robot.executed_t", "policy.action_t"):
        if not semantics.get(key):
            errors.append(f"metadata.action_semantics.{key} is missing")


def _validate_c2_core_thresholds(
    tables: dict[str, object], metadata: dict[str, object], checks: list[dict[str, object]],
    errors: list[str], metrics: dict[str, object],
) -> None:
    coverage = metrics.get("sample_sync.coverage_rate")
    _add_check(
        checks, errors, name="sample_coverage", value=float(coverage) if isinstance(coverage, (int, float)) else None,
        threshold={"min": C2_VALIDATION_THRESHOLDS["sample_coverage_min"]},
        passed=None if not isinstance(coverage, (int, float)) else float(coverage) >= C2_VALIDATION_THRESHOLDS["sample_coverage_min"],
        reason_code="sample_coverage.unavailable" if coverage is None else "sample_coverage.below_minimum",
        detail="sample coverage is unavailable" if coverage is None else f"sample coverage {float(coverage):.6f} is below 1.0",
    )
    control_metrics = metrics.get("streams.control")
    expected_rate = metadata.get("control_rate_hz")
    if not isinstance(expected_rate, (int, float)):
        expected_rate = metadata.get("configured_control_rate_hz")
    measured_rate = control_metrics.get("measured_frequency_hz") if isinstance(control_metrics, dict) else None
    if isinstance(expected_rate, (int, float)) and float(expected_rate) > 0 and isinstance(measured_rate, (int, float)):
        lower = float(expected_rate) * C2_VALIDATION_THRESHOLDS["control_rate_min_ratio"]
        upper = float(expected_rate) * C2_VALIDATION_THRESHOLDS["control_rate_max_ratio"]
        passed = lower <= float(measured_rate) <= upper
        detail = f"control frequency {float(measured_rate):.3f} Hz must be within [{lower:.3f}, {upper:.3f}] Hz"
    else:
        lower = upper = None
        passed = None
        detail = "configured or measured control frequency is unavailable"
    _add_check(checks, errors, name="control_loop_frequency", value=float(measured_rate) if isinstance(measured_rate, (int, float)) else None,
               threshold={"min_hz": lower, "max_hz": upper}, passed=passed,
               reason_code="control.frequency_unavailable" if passed is None else "control.frequency_out_of_range", detail=detail)
    jitter = control_metrics.get("jitter_ms", {}).get("p95") if isinstance(control_metrics, dict) else None
    _add_check(checks, errors, name="control_loop_jitter_p95", value=jitter,
               threshold={"max_ms": C2_VALIDATION_THRESHOLDS["control_jitter_p95_ms_max"]},
               passed=None if jitter is None else float(jitter) <= C2_VALIDATION_THRESHOLDS["control_jitter_p95_ms_max"],
               reason_code="control.jitter_unavailable" if jitter is None else "control.jitter_exceeded",
               detail="control jitter is unavailable" if jitter is None else f"control jitter P95 is {jitter} ms")
    max_gap = control_metrics.get("gap_ms", {}).get("max") if isinstance(control_metrics, dict) else None
    _add_check(checks, errors, name="control_loop_max_gap", value=max_gap,
               threshold={"max_ms": C2_VALIDATION_THRESHOLDS["control_max_gap_ms"]},
               passed=None if max_gap is None else float(max_gap) <= C2_VALIDATION_THRESHOLDS["control_max_gap_ms"],
               reason_code="control.gap_unavailable" if max_gap is None else "control.max_gap_exceeded",
               detail="control max gap is unavailable" if max_gap is None else f"control max gap is {max_gap} ms")

    lifecycle_specs = {
        "command_send_latency_ms": ("control", "action_send_start_host_monotonic_ns", "action_send_end_host_monotonic_ns"),
        "command_result_latency_ms": ("control", "action_send_end_host_monotonic_ns", "action_send_result_received_host_monotonic_ns"),
        "feedback_latency_ms": ("robot_feedback", "robot_feedback_read_start_host_monotonic_ns", "robot_feedback_host_receive_monotonic_ns"),
        "feedback_enqueue_latency_ms": ("robot_feedback", "robot_feedback_host_receive_monotonic_ns", "robot_feedback_enqueue_host_monotonic_ns"),
        "quest_enqueue_latency_ms": ("vr_input", "controller_event_host_receive_monotonic_ns", "controller_event_enqueue_host_monotonic_ns"),
    }
    for name, (stream, start, end) in lifecycle_specs.items():
        table = tables.get(stream)
        metrics[f"latency.{name}"] = _latency_distribution(table.to_pylist(), start, end) if table is not None else _distribution([], unit="ms")

    vr_table = tables.get("vr_input")
    quest_ages = [float(row["controller_event_age_s"]) * 1000 for row in vr_table.to_pylist() if isinstance(row.get("controller_event_age_s"), (int, float))] if vr_table is not None else []
    metrics["latency.quest_stale_age_ms"] = _distribution(quest_ages, unit="ms")
    quest_p95 = metrics["latency.quest_stale_age_ms"]["p95"]
    _add_check(checks, errors, name="quest_stale_age_p95", value=quest_p95,
               threshold={"max_ms": C2_VALIDATION_THRESHOLDS["quest_stale_age_p95_ms_max"]},
               passed=None if quest_p95 is None else float(quest_p95) <= C2_VALIDATION_THRESHOLDS["quest_stale_age_p95_ms_max"],
               reason_code="quest.stale_unavailable" if quest_p95 is None else "quest.stale_exceeded",
               detail="Quest stale age is unavailable" if quest_p95 is None else f"Quest stale age P95 is {quest_p95} ms")

    robot_table = tables.get("robot_feedback")
    feedback_ages = [
        (int(row["host_monotonic_ns"]) - int(row["robot_feedback_host_receive_monotonic_ns"])) / 1e6
        for row in robot_table.to_pylist()
        if isinstance(row.get("host_monotonic_ns"), int) and isinstance(row.get("robot_feedback_host_receive_monotonic_ns"), int)
        and int(row["host_monotonic_ns"]) >= int(row["robot_feedback_host_receive_monotonic_ns"])
    ] if robot_table is not None else []
    metrics["latency.robot_feedback_stale_age_ms"] = _distribution(feedback_ages, unit="ms")
    feedback_p95 = metrics["latency.robot_feedback_stale_age_ms"]["p95"]
    _add_check(checks, errors, name="robot_feedback_stale_p95", value=feedback_p95,
               threshold={"max_ms": C2_VALIDATION_THRESHOLDS["robot_feedback_stale_p95_ms_max"]},
               passed=None if feedback_p95 is None else float(feedback_p95) <= C2_VALIDATION_THRESHOLDS["robot_feedback_stale_p95_ms_max"],
               reason_code="robot_feedback.stale_unavailable" if feedback_p95 is None else "robot_feedback.stale_exceeded",
               detail="robot feedback stale age is unavailable" if feedback_p95 is None else f"robot feedback stale age P95 is {feedback_p95} ms")


def _validate_c2_camera_thresholds(
    rows: list[dict[str, object]], required: bool, checks: list[dict[str, object]],
    errors: list[str], metrics: dict[str, object],
) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        camera_name = row.get("camera_name")
        if isinstance(camera_name, str):
            counts[camera_name] = counts.get(camera_name, 0) + 1
    frame_count_skew = max(counts.values()) - min(counts.values()) if counts else None
    _add_check(checks, errors, name="camera_frame_count_skew", value=frame_count_skew,
               threshold={"max_frames": C2_VALIDATION_THRESHOLDS["camera_frame_count_skew_max"]},
               passed=None if frame_count_skew is None else frame_count_skew <= C2_VALIDATION_THRESHOLDS["camera_frame_count_skew_max"],
               reason_code="camera.frame_count_skew_unavailable" if frame_count_skew is None else "camera.frame_count_skew_exceeded",
               detail="camera frame count skew is unavailable" if frame_count_skew is None else f"camera frame count skew is {frame_count_skew} frames")
    metrics["latency.camera_receive_ms"] = _latency_distribution(rows, "camera_sensor_timestamp_ns", "camera_host_receive_monotonic_ns")
    metrics["latency.camera_enqueue_ms"] = _latency_distribution(rows, "camera_host_receive_monotonic_ns", "camera_enqueue_host_monotonic_ns")
    metrics["latency.camera_write_ms"] = _latency_distribution(rows, "camera_enqueue_host_monotonic_ns", "camera_write_host_monotonic_ns")
    write_p95 = metrics["latency.camera_write_ms"]["p95"]
    _add_check(checks, errors, name="camera_write_latency_p95", value=write_p95,
               threshold={"max_ms": C2_VALIDATION_THRESHOLDS["camera_write_latency_p95_ms_max"]},
               passed=None if write_p95 is None else float(write_p95) <= C2_VALIDATION_THRESHOLDS["camera_write_latency_p95_ms_max"],
               reason_code="camera.write_latency_unavailable" if write_p95 is None else "camera.write_latency_exceeded",
               detail="camera write latency is unavailable" if write_p95 is None else f"camera write latency P95 is {write_p95} ms")
    grouped: dict[int, list[int]] = {}
    for row in rows:
        sequence = row.get("camera_stream_sequence_id")
        stamp = row.get("host_monotonic_ns")
        if isinstance(sequence, int) and isinstance(stamp, int):
            grouped.setdefault(sequence, []).append(stamp)
    skews = [(max(stamps) - min(stamps)) / 1e6 for stamps in grouped.values() if len(stamps) >= 2]
    metrics["camera.multicamera_sync_ms"] = _distribution(skews, unit="ms")
    max_skew = metrics["camera.multicamera_sync_ms"]["max"]
    _add_check(checks, errors, name="multicamera_sync", value=max_skew,
               threshold={"max_ms": C2_VALIDATION_THRESHOLDS["multicamera_sync_ms_max"]},
               passed=None if max_skew is None else float(max_skew) <= C2_VALIDATION_THRESHOLDS["multicamera_sync_ms_max"],
               reason_code="camera.multicamera_sync_unavailable" if max_skew is None else "camera.multicamera_sync_exceeded",
               detail="multicamera sync is unavailable" if max_skew is None else f"multicamera max sync skew is {max_skew} ms")


def _schema_names(table: object) -> set[str]:
    return set(getattr(getattr(table, "schema", None), "names", []) or [])


def _validate_lifecycle_contract(
    table: object, stream: str, cleaning_ready: bool, errors: list[str]
) -> None:
    if not cleaning_ready:
        return
    names = _schema_names(table)
    missing = [field for field in LIFECYCLE_ROW_FIELDS[stream] if field not in names]
    if missing:
        errors.append(f"{stream} is missing lifecycle fields: {', '.join(missing)}")
        return
    ordered_fields = {
        "control": (
            "action_request_generated_host_monotonic_ns", "action_send_start_host_monotonic_ns",
            "action_send_end_host_monotonic_ns", "action_send_result_received_host_monotonic_ns",
        ),
        "robot_feedback": (
            "robot_feedback_read_start_host_monotonic_ns", "robot_feedback_host_receive_monotonic_ns",
            "robot_feedback_enqueue_host_monotonic_ns",
        ),
        "camera": (
            "camera_host_receive_monotonic_ns", "camera_enqueue_host_monotonic_ns",
            "camera_write_host_monotonic_ns",
        ),
    }
    source_fields = {
        "robot_feedback": ("robot_feedback_source_timestamp_ns", "robot_feedback_source_timestamp_unavailable_reason"),
        "vr_input": ("controller_event_source_timestamp_ns", "controller_event_source_timestamp_unavailable_reason"),
        "camera": ("camera_sensor_timestamp_ns", "camera_sensor_timestamp_unavailable_reason"),
    }
    for index, row in enumerate(table.to_pylist()):
        fields = ordered_fields.get(stream)
        if fields:
            values = [row.get(field) for field in fields]
            if not all(isinstance(value, int) and value > 0 for value in values):
                errors.append(f"{stream} row {index} has invalid lifecycle timestamps")
            elif any(left > right for left, right in zip(values, values[1:])):
                errors.append(f"{stream} row {index} lifecycle timestamps are reversed")
        source = source_fields.get(stream)
        if source and row.get(source[0]) is None and not row.get(source[1]):
            errors.append(f"{stream} row {index} lacks source timestamp and unavailable reason")


def _validate_core_row_contract(
    table: object,
    filename: str,
    *,
    cleaning_ready: bool,
    require_sample_id: bool,
    errors: list[str],
    warnings: list[str],
    metrics: dict[str, object],
) -> None:
    names = _schema_names(table)
    required = list(CORE_ROW_FIELDS)
    if not require_sample_id:
        required.remove("sample_id")
        required.remove("control_sample_index")
    missing = [field for field in required if field not in names]
    if missing and cleaning_ready:
        errors.append(f"{filename} is missing cleaning-ready fields: {', '.join(missing)}")
    elif missing:
        warnings.append(f"{filename} uses legacy row contract; missing: {', '.join(missing)}")
    if "row_sequence_id" in names:
        sequence = [int(value) for value in table.column("row_sequence_id").to_pylist()]
        if not _strictly_increasing(sequence):
            errors.append(f"{filename} row_sequence_id is not strictly increasing")
        metrics[f"{filename}.row_sequence_max"] = max(sequence) if sequence else -1
    if "source_timestamp_ns" in names:
        invalid_source_times = sum(
            value is None or int(value) <= 0
            for value in table.column("source_timestamp_ns").to_pylist()
        )
        metrics[f"{filename}.invalid_source_timestamp_count"] = invalid_source_times
        if cleaning_ready and invalid_source_times:
            errors.append(f"{filename} contains invalid source_timestamp_ns values")


def _validate_sample_sync(
    tables: dict[str, object],
    cleaning_ready: bool,
    errors: list[str],
    warnings: list[str],
    metrics: dict[str, object],
) -> None:
    sample_sets: dict[str, set[str]] = {}
    for stream in SAMPLE_SYNC_STREAMS:
        table = tables.get(stream)
        if table is None or "sample_id" not in _schema_names(table):
            continue
        values = [str(value) for value in table.column("sample_id").to_pylist()]
        sample_sets[stream] = set(values)
        metrics[f"sample_sync.{stream}.sample_count"] = len(values)
        metrics[f"sample_sync.{stream}.unique_sample_count"] = len(sample_sets[stream])
        if len(values) != len(sample_sets[stream]):
            errors.append(f"{stream}.parquet contains duplicate sample_id values")

    if set(sample_sets) != set(SAMPLE_SYNC_STREAMS):
        if cleaning_ready:
            errors.append("cleaning-ready sample_id coverage is missing one or more core streams")
        return

    common = set.intersection(*(sample_sets[stream] for stream in SAMPLE_SYNC_STREAMS))
    union = set.union(*(sample_sets[stream] for stream in SAMPLE_SYNC_STREAMS))
    metrics["sample_sync.common_sample_count"] = len(common)
    metrics["sample_sync.union_sample_count"] = len(union)
    metrics["sample_sync.coverage_rate"] = len(common) / max(len(union), 1)
    control_missing = {
        stream: sorted(sample_sets["control"] - sample_sets[stream])[:10]
        for stream in SAMPLE_SYNC_STREAMS
        if stream != "control"
    }
    if any(control_missing.values()):
        message = f"control sample_id values are missing from paired streams: {control_missing}"
        if cleaning_ready:
            errors.append(message)
        else:
            warnings.append(message)
