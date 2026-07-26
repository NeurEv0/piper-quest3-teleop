"""No-hardware deterministic Canonical Raw fixtures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .action_state import ACTION_KEYS, typed_control_fields, typed_feedback_fields
from .contract import (
    CAPTURE_CONTRACT_VERSION,
    default_action_semantics,
    default_action_space_contract,
    default_timebase_contract,
    session_event,
)
from .validator import validate_episode


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":"), default=_json_default) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_parquet(path: Path, rows: list[dict[str, object]]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


def _write_video(path: Path, frames: list[Any], fps: float) -> None:
    import cv2
    import numpy as np

    if not frames:
        raise ValueError("video requires at least one frame")
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"failed to open video writer for {path.name}")
    try:
        for frame in frames:
            array = np.asarray(frame, dtype=np.uint8)
            writer.write(cv2.cvtColor(array, cv2.COLOR_RGB2BGR))
    finally:
        writer.release()


def write_c1_session_fixtures(root: Path) -> list[Path]:
    scenarios = {
        "normal_completion": [("operator_start", "episode_0", None, 0), ("success", "episode_0", "operator_success", 10)],
        "operator_abort": [("operator_start", "episode_0", None, 0), ("operator_abort", "episode_0", "operator_abort", 10)],
        "process_interruption": [("operator_start", "episode_0", None, 0), ("process_interruption", "episode_0", "signal", 10)],
        "task_change": [("operator_start", "episode_0", None, 0), ("task_change", "episode_0", "task_change", 10), ("operator_start", "episode_1", None, 10), ("success", "episode_1", "operator_success", 20)],
        "time_gap_slicing": [("operator_start", "episode_0", None, 0), ("success", "episode_0", "time_gap", 10), ("operator_start", "episode_1", None, 100), ("success", "episode_1", "operator_success", 110)],
    }
    paths: list[Path] = []
    for name, definitions in scenarios.items():
        session_dir = Path(root) / name
        session_dir.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        for sequence, (event_type, episode_id, reason, offset) in enumerate(definitions):
            row = session_event(event_type, episode_id=episode_id, reason=reason, source="fixture")
            row.update({"session_event_sequence_id": sequence, "host_monotonic_ns": 1_000_000_000 + offset * 1_000_000_000, "host_wall_time_ns": 2_000_000_000 + offset * 1_000_000_000, "event": event_type})
            rows.append(row)
        (session_dir / "session_events.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        _write_json(session_dir / "session.json", {"session_id": name, "fixture": True})
        paths.append(session_dir)
    return paths


def _neutral_action() -> dict[str, float]:
    action = {key: 0.0 for key in ACTION_KEYS}
    action["left_gripper.pos"] = 0.04
    action["right_gripper.pos"] = 0.04
    return action


def _build_cleaning_ready_metadata(
    *,
    session_id: str,
    episode_id: str,
    start_ns: int,
    end_ns: int,
    calibration: dict[str, Any],
    calibration_path: Path,
    calibration_sha256: str,
    camera_config: list[dict[str, Any]],
) -> dict[str, Any]:
    calibration_summary = {
        "version": calibration["calibration_version"],
        "status": calibration["status"],
        "tf_status": calibration["tf_status"],
        "source_file": "calibration/rig_current.json",
        "source_sha256": calibration_sha256,
        "frame_ids": calibration["frame_ids"],
        "camera_serials": calibration["camera_serials"],
        "camera_intrinsics": calibration["camera_intrinsics"],
        "transforms": calibration["transforms"],
        "dynamic_tf": calibration["dynamic_tf"],
        "limitations": calibration["limitations"],
    }
    return {
        "schema_version": "piper_canonical_raw_v1",
        "capture_contract_version": CAPTURE_CONTRACT_VERSION,
        "episode_id": episode_id,
        "session_id": session_id,
        "operator_id": "fixture_operator",
        "task_id": "fixture_bimanual_pick_place",
        "robot_id": calibration["robot_id"],
        "scene_id": "fixture_scene",
        "language_instruction": "Move the blue block to the target tray.",
        "task": "Move the blue block to the target tray.",
        "annotation_schema": "piper.vla.language.v1",
        "camera_mode": "mosaic",
        "camera_config": camera_config,
        "calibration_version": calibration["calibration_version"],
        "calibration_status": calibration["status"],
        "calibration_source": "calibration/rig_current.json",
        "calibration": calibration_summary,
        "robot_urdf_version": "piper-fixture-v1",
        "teleop_commit": "fixture000",
        "control_commit": "fixture000",
        "action_space": default_action_space_contract(),
        "collection_profile": {
            "version": "piper_bimanual_quest3_cleaning_ready_v1",
            "record_action_from_follower": True,
            "teleop_joint_alpha": 1.0,
            "teleop_gripper_alpha": 1.0,
            "max_joint_delta_rad_per_sample": 0.02,
            "max_gripper_delta_m_per_sample": 0.002,
            "safety_clamp_behavior": "delegate_to_robot_adapter_and_record_result",
            "hardware_velocity_limits": {"status": "unavailable", "reason": "fixture"},
        },
        "timebase": default_timebase_contract(),
        "action_semantics_version": "piper_action_semantics_v1",
        "action_semantics": default_action_semantics(),
        "capture_mode": "cleaning_ready",
        "control_rate_hz": 30.0,
        "camera_rate_hz": 30.0,
        "start_host_monotonic_ns": start_ns,
        "episode_start_host_monotonic_ns": start_ns,
        "end_host_monotonic_ns": end_ns,
        "episode_end_host_monotonic_ns": end_ns,
        "duration_s": round((end_ns - start_ns) / 1e9, 6),
        "slicing_rule": "session_event_episode_boundaries_v1",
        "recording_state": "finalized",
        "termination_reason": "operator_success",
        "failure_reason": "none",
        "task_success": True,
        "cleaning_ready": True,
        "cleaning_ready_reason_codes": [],
        "preflight": {"blocked": False, "checks": []},
        "stream_counts": {"control": 12, "robot_feedback": 12, "vr_input": 12, "camera": 36, "event": 2},
        "stream_sequence_counts": {"control": 12, "robot_feedback": 12, "vr_input": 12, "camera": 36, "event": 2, "language_action": 0},
        "camera_sync": {"camera_frame_counts": {"cam_front": 12, "cam_left_wrist": 12, "cam_right_wrist": 12}, "max_frame_count_skew": 0, "first_frame_skew_ms": 0.0, "last_frame_skew_ms": 0.0},
        "mcap_log": {"enabled": False, "status": "disabled", "error": None},
        "runtime_failures": [],
    }


def write_cleaning_ready_fixture(root: Path) -> list[Path]:
    import numpy as np

    root = Path(root)
    session_id = "session_cleaning_ready_fixture"
    episode_id = "episode_cleaning_ready_fixture"
    session_dir = root / session_id
    episode_dir = session_dir / episode_id
    episode_dir.mkdir(parents=True, exist_ok=True)

    calibration_path = Path(__file__).resolve().parent.parent / "calibration" / "rig_current.json"
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    calibration_sha256 = _sha256(calibration_path)
    calibration = {**calibration, "source_file": "calibration/rig_current.json", "source_sha256": calibration_sha256}
    calibration_file = episode_dir / "calibration_snapshot.json"
    _write_json(calibration_file, calibration)

    camera_config = [
        {"name": name, "serial": calibration["camera_serials"][name], "width": 160, "height": 120, "fps": 30.0, "optical_frame": calibration["frame_ids"][name], "frame_id": calibration["frame_ids"][name]}
        for name in ("cam_front", "cam_left_wrist", "cam_right_wrist")
    ]

    start_ns = 1_700_000_000_000_000_000
    frame_period_ns = 33_333_333
    end_ns = start_ns + 11 * frame_period_ns + 20_000_000

    metadata = _build_cleaning_ready_metadata(
        session_id=session_id,
        episode_id=episode_id,
        start_ns=start_ns,
        end_ns=end_ns,
        calibration=calibration,
        calibration_path=calibration_path,
        calibration_sha256=calibration_sha256,
        camera_config=camera_config,
    )
    _write_json(session_dir / "session.json", {"session_id": session_id, "schema_version": "piper_canonical_raw_v1", "fixture": True, "episode_id": episode_id, "operator_id": metadata["operator_id"], "task_id": metadata["task_id"]})
    _write_json(episode_dir / "metadata.json", metadata)

    session_events: list[dict[str, Any]] = []
    for sequence, envelope in enumerate(
        [
            session_event("session_start", source="fixture", payload={"session_id": session_id}),
            session_event(
                "operator_start",
                episode_id=episode_id,
                source="fixture",
                payload={"task_id": metadata["task_id"], "operator_id": metadata["operator_id"]},
            ),
            session_event("success", episode_id=episode_id, reason="operator_success", source="fixture", payload={"task_success": True}),
            session_event("operator_stop", episode_id=episode_id, reason="operator_success", source="fixture"),
            session_event("process_shutdown", reason="orderly_shutdown", source="fixture", payload={"session_id": session_id}),
        ]
    ):
        session_events.append(
            {
                "session_event_sequence_id": sequence,
                "host_monotonic_ns": start_ns + sequence * 1_000_000,
                "host_wall_time_ns": start_ns + sequence * 1_000_000 + 10_000_000_000,
                "event": envelope["event_type"],
                **envelope,
            }
        )
    _write_jsonl(session_dir / "session_events.jsonl", session_events)

    zero_action = _neutral_action()
    control_rows: list[dict[str, Any]] = []
    robot_rows: list[dict[str, Any]] = []
    vr_rows: list[dict[str, Any]] = []
    camera_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = [
        {
            "host_monotonic_ns": start_ns,
            "host_wall_time_ns": start_ns + 10_000_000_000,
            "event": "recording_started",
            "event_type": "recording_started",
            "episode_id": episode_id,
            "source": "fixture",
            "payload": {"capture_contract_version": CAPTURE_CONTRACT_VERSION},
        },
        {
            "host_monotonic_ns": end_ns,
            "host_wall_time_ns": end_ns + 10_000_000_000,
            "event": "recording_finished",
            "event_type": "recording_finished",
            "episode_id": episode_id,
            "source": "fixture",
            "reason": "operator_success",
            "payload": {"task_success": True},
        },
    ]

    camera_frames: dict[str, list[Any]] = {"cam_front": [], "cam_left_wrist": [], "cam_right_wrist": []}
    for index in range(12):
        sample_id = f"{episode_id}:{index:06d}"
        host_ns = start_ns + index * frame_period_ns
        wall_ns = host_ns + 10_000_000_000
        control_rows.append(
            {
                "sample_id": sample_id,
                "control_sample_index": index,
                "row_sequence_id": index,
                "host_monotonic_ns": host_ns,
                "host_wall_time_ns": wall_ns,
                "source_timestamp_ns": host_ns,
                "teleop_sample_host_monotonic_ns": host_ns - 500_000,
                "robot_feedback_host_monotonic_ns": host_ns,
                "phase": "teleop",
                "left_mode": "TELEOP",
                "right_mode": "TELEOP",
                "action_request_generated_host_monotonic_ns": host_ns - 2_000_000,
                "action_send_start_host_monotonic_ns": host_ns - 1_500_000,
                "action_send_end_host_monotonic_ns": host_ns - 1_000_000,
                "action_send_result_received_host_monotonic_ns": host_ns - 500_000,
                "action_requested": zero_action,
                "action_sent": zero_action,
                "action_requested_json": json.dumps(zero_action, separators=(",", ":")),
                "action_sent_json": json.dumps(zero_action, separators=(",", ":")),
                **typed_control_fields(zero_action, zero_action),
                "safety_state": "normal",
            }
        )
        robot_rows.append(
            {
                "sample_id": sample_id,
                "control_sample_index": index,
                "row_sequence_id": index,
                "host_monotonic_ns": host_ns,
                "host_wall_time_ns": wall_ns,
                "source_timestamp_ns": host_ns,
                "robot_feedback_source_timestamp_ns": None,
                "robot_feedback_source_timestamp_unavailable_reason": "hardware_timestamp_unavailable",
                "robot_feedback_read_start_host_monotonic_ns": host_ns - 2_000_000,
                "robot_feedback_host_receive_monotonic_ns": host_ns,
                "robot_feedback_enqueue_host_monotonic_ns": host_ns + 1_000_000,
                "observation": zero_action,
                "observation_json": json.dumps(zero_action, separators=(",", ":")),
                **typed_feedback_fields(zero_action, zero_action),
            }
        )
        vr_rows.append(
            {
                "sample_id": sample_id,
                "control_sample_index": index,
                "row_sequence_id": index,
                "host_monotonic_ns": host_ns,
                "host_wall_time_ns": wall_ns,
                "source_timestamp_ns": host_ns,
                "controller_event_source_timestamp_ns": None,
                "controller_event_source_timestamp_unavailable_reason": "quest_device_timestamp_unavailable",
                "controller_event_host_receive_monotonic_ns": None,
                "controller_event_host_receive_unavailable_reason": "no_controller_event",
                "controller_event_enqueue_host_monotonic_ns": host_ns + 500_000,
                "controller_event_age_s": 0.01,
                "controller_event_count": index + 1,
                "left_pose_xyzw": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                "right_pose_xyzw": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
                "left_state": {"buttons": {"trigger": False, "grip": False}},
                "right_state": {"buttons": {"trigger": False, "grip": False}},
            }
        )
        for camera_index, camera_name in enumerate(("cam_front", "cam_left_wrist", "cam_right_wrist")):
            camera_host_ns = host_ns + camera_index * 5_000_000
            camera_enqueue_ns = camera_host_ns + 1_000_000
            camera_write_ns = camera_enqueue_ns + 2_000_000
            camera_rows.append(
                {
                    "sample_id": sample_id,
                    "control_sample_index": index,
                    "row_sequence_id": len(camera_rows),
                    "host_monotonic_ns": camera_host_ns,
                    "host_wall_time_ns": camera_host_ns + 10_000_000_000,
                    "source_timestamp_ns": camera_host_ns,
                    "camera_name": camera_name,
                    "camera_stream_sequence_id": index,
                    "camera_sensor_timestamp_ns": None,
                    "camera_sensor_timestamp_unavailable_reason": "camera_sdk_timestamp_unavailable",
                    "camera_host_receive_monotonic_ns": camera_host_ns,
                    "camera_enqueue_host_monotonic_ns": camera_enqueue_ns,
                    "camera_write_host_monotonic_ns": camera_write_ns,
                    "optical_frame": calibration["frame_ids"][camera_name],
                    "image_ref": f"camera_{camera_name}.mp4#{index:06d}",
                    "decoded": True,
                    "width": 160,
                    "height": 120,
                    "video_frame_index": index,
                }
            )
            frame = np.zeros((120, 160, 3), dtype=np.uint8)
            palette = {
                "cam_front": (220, 60, 60),
                "cam_left_wrist": (60, 220, 60),
                "cam_right_wrist": (60, 60, 220),
            }[camera_name]
            frame[:] = palette
            frame[:, (index * 11) % 160 : ((index * 11) % 160) + 12] = (245, 245, 245)
            camera_frames[camera_name].append(frame)

    _write_parquet(episode_dir / "control.parquet", control_rows)
    _write_parquet(episode_dir / "robot_feedback.parquet", robot_rows)
    _write_parquet(episode_dir / "vr_input.parquet", vr_rows)
    _write_parquet(episode_dir / "camera_timestamps.parquet", camera_rows)
    _write_parquet(episode_dir / "events.parquet", event_rows)
    _write_jsonl(episode_dir / "events.jsonl", event_rows)
    for camera_name, frames in camera_frames.items():
        _write_video(episode_dir / f"camera_{camera_name}.mp4", frames, 30.0)

    report = validate_episode(episode_dir)
    if not report.valid:
        raise RuntimeError(f"generated cleaning-ready fixture is invalid: {report.errors}")
    _write_json(episode_dir / "validation.json", report.to_dict())

    files: list[dict[str, Any]] = []
    for item in sorted(episode_dir.iterdir()):
        if item.is_file() and item.name != "manifest.json":
            files.append({"path": item.name, "bytes": item.stat().st_size, "sha256": _sha256(item)})
    manifest = {
        "schema_version": "piper_canonical_raw_v1",
        "episode_id": episode_id,
        "created_host_monotonic_ns": end_ns,
        "files": files,
        "stream_counts": metadata["stream_counts"],
        "stream_sequence_counts": metadata["stream_sequence_counts"],
        "camera_sync": metadata["camera_sync"],
    }
    _write_json(episode_dir / "manifest.json", manifest)

    return [session_dir, episode_dir]
