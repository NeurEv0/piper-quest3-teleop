"""No-hardware fault injection for C2 timing and synchronization gates."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable

import pyarrow as pa
import pyarrow.parquet as pq

from canonical_raw.contract import CAPTURE_CONTRACT_VERSION, default_action_semantics, default_timebase_contract
from canonical_raw.validator import validate_episode


def _episode(root: Path) -> Path:
    root.mkdir(parents=True)
    start = 1_000_000_000
    metadata = {
        "episode_id": "episode_c2", "operator_id": "op", "task_id": "task",
        "schema_version": "piper_canonical_raw_v1", "capture_contract_version": CAPTURE_CONTRACT_VERSION,
        "start_host_monotonic_ns": start, "episode_start_host_monotonic_ns": start,
        "episode_end_host_monotonic_ns": start + 200_000_000, "duration_s": 0.2,
        "termination_reason": "operator_success", "slicing_rule": "session_event_episode_boundaries_v1",
        "camera_mode": "off", "control_rate_hz": 30.0,
        "timebase": default_timebase_contract(), "action_semantics": default_action_semantics(),
    }
    (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    rows = {stream: [] for stream in ("control", "robot_feedback", "vr_input")}
    for index in range(6):
        stamp = start + index * 33_333_333
        common = {"sample_id": f"episode_c2:{index:06d}", "control_sample_index": index, "row_sequence_id": index,
                  "host_monotonic_ns": stamp, "host_wall_time_ns": stamp + 10_000_000_000, "source_timestamp_ns": stamp}
        rows["control"].append({**common, "action_request_generated_host_monotonic_ns": stamp,
                                "action_send_start_host_monotonic_ns": stamp + 1_000_000,
                                "action_send_end_host_monotonic_ns": stamp + 2_000_000,
                                "action_send_result_received_host_monotonic_ns": stamp + 3_000_000})
        rows["robot_feedback"].append({**common, "robot_feedback_source_timestamp_ns": None,
                                       "robot_feedback_source_timestamp_unavailable_reason": "hardware_timestamp_unavailable",
                                       "robot_feedback_read_start_host_monotonic_ns": stamp - 2_000_000,
                                       "robot_feedback_host_receive_monotonic_ns": stamp,
                                       "robot_feedback_enqueue_host_monotonic_ns": stamp + 1_000_000})
        rows["vr_input"].append({**common, "controller_event_source_timestamp_ns": None,
                                 "controller_event_source_timestamp_unavailable_reason": "quest_device_timestamp_unavailable",
                                 "controller_event_host_receive_monotonic_ns": stamp - 1_000_000,
                                 "controller_event_host_receive_unavailable_reason": None,
                                 "controller_event_enqueue_host_monotonic_ns": stamp,
                                 "controller_event_age_s": 0.01, "controller_event_count": index + 1})
    for stream, values in rows.items():
        pq.write_table(pa.Table.from_pylist(values), root / f"{stream}.parquet")
    return root


def _mutate(path: Path, callback: Callable[[list[dict[str, object]]], None]) -> None:
    rows = pq.read_table(path).to_pylist()
    callback(rows)
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_stable_reason_codes_for_regression_lifecycle_source_and_duplicate() -> None:
    cases = {
        "regression": ("control", lambda rows: rows[2].update(host_monotonic_ns=int(rows[1]["host_monotonic_ns"]) - 1), "timestamp.regression"),
        "lifecycle": ("control", lambda rows: rows[1].update(action_send_start_host_monotonic_ns=int(rows[1]["action_send_end_host_monotonic_ns"]) + 1), "lifecycle.order_reversed"),
        "source": ("robot_feedback", lambda rows: rows[0].update(robot_feedback_source_timestamp_ns=None, robot_feedback_source_timestamp_unavailable_reason=None), "source_timestamp.reason_missing"),
        "duplicate": ("control", lambda rows: rows[1].update(sample_id=rows[0]["sample_id"]), "sample_id.duplicate"),
    }
    with tempfile.TemporaryDirectory(prefix="c2_codes_") as temp:
        for name, (stream, callback, code) in cases.items():
            episode = _episode(Path(temp) / name)
            _mutate(episode / f"{stream}.parquet", callback)
            first = validate_episode(episode, require_cameras=False)
            second = validate_episode(episode, require_cameras=False)
            assert not first.valid and code in first.reason_codes
            assert first.reason_codes == second.reason_codes


def test_gap_drop_quest_and_feedback_stale_thresholds() -> None:
    with tempfile.TemporaryDirectory(prefix="c2_thresholds_") as temp:
        episode = _episode(Path(temp) / "episode")
        def control_fault(rows: list[dict[str, object]]) -> None:
            for row in rows[2:]:
                row["host_monotonic_ns"] = int(row["host_monotonic_ns"]) + 200_000_000
            rows[3]["control_sample_index"] = 4
        _mutate(episode / "control.parquet", control_fault)
        _mutate(episode / "vr_input.parquet", lambda rows: [row.update(controller_event_age_s=0.2) for row in rows])
        _mutate(episode / "robot_feedback.parquet", lambda rows: [row.update(robot_feedback_host_receive_monotonic_ns=int(row["host_monotonic_ns"]) - 200_000_000) for row in rows])
        report = validate_episode(episode, require_cameras=False)
        assert {"control.max_gap_exceeded", "quest.stale_exceeded", "robot_feedback.stale_exceeded"} <= set(report.reason_codes)
        assert report.metrics["streams.control"]["drop_count"] == 1


def test_camera_write_and_multicamera_skew_thresholds() -> None:
    with tempfile.TemporaryDirectory(prefix="c2_camera_") as temp:
        episode = _episode(Path(temp) / "episode")
        rows = []
        for index in range(3):
            for camera_index, camera in enumerate(("cam_front", "cam_left_wrist", "cam_right_wrist")):
                stamp = 1_000_000_000 + index * 33_333_333 + camera_index * 50_000_000
                enqueue = stamp + 1_000_000
                rows.append({"row_sequence_id": len(rows), "host_monotonic_ns": stamp, "host_wall_time_ns": stamp,
                             "source_timestamp_ns": stamp, "camera_name": camera, "camera_stream_sequence_id": index,
                             "camera_sensor_timestamp_ns": None, "camera_sensor_timestamp_unavailable_reason": "sdk_unavailable",
                             "camera_host_receive_monotonic_ns": stamp, "camera_enqueue_host_monotonic_ns": enqueue,
                             "camera_write_host_monotonic_ns": enqueue + (150_000_000 if camera == "cam_front" else 1_000_000),
                             "decoded": True})
        pq.write_table(pa.Table.from_pylist(rows), episode / "camera_timestamps.parquet")
        report = validate_episode(episode, require_cameras=False)
        assert {"camera.write_latency_exceeded", "camera.multicamera_sync_exceeded"} <= set(report.reason_codes)

