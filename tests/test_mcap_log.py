#!/usr/bin/env python3
"""No-hardware integration test for the optional MCAP log layer."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import numpy as np

from canonical_raw.recorder import AsyncCanonicalRecorder
from canonical_raw.validator import validate_episode
from mcap_log.validator import validate_mcap


def test_canonical_raw_with_mcap_sidecar() -> None:
    calibration = json.loads(
        (Path(__file__).resolve().parent.parent / "calibration" / "rig_current.json").read_text(encoding="utf-8")
    )
    with tempfile.TemporaryDirectory(prefix="mcap_log_test_") as temp:
        recorder = AsyncCanonicalRecorder(
            output_root=Path(temp),
            session_metadata={"test": True},
            camera_fps=10.0,
            enable_mcap=True,
            calibration_snapshot=calibration,
        )
        try:
            recorder.start_episode(
                {
                    "episode_id": "episode_mcap_test",
                    "operator_id": "test_operator",
                    "task_id": "test_task",
                    "robot_id": "test_robot",
                    "scene_id": "test_scene",
                    "camera_mode": "mosaic",
                    "language_instruction": "Pick up the test object and place it on the target.",
                    "annotation_schema": "piper.vla.language.v1",
                }
            )
            rng = np.random.default_rng(11)
            for index in range(8):
                now_mono = time.monotonic_ns() + index
                now_wall = time.time_ns() + index
                sample_id = f"episode_mcap_test:{index:06d}"
                recorder.record_row(
                    "control",
                    {
                        "sample_id": sample_id,
                        "control_sample_index": index,
                        "host_monotonic_ns": now_mono,
                        "host_wall_time_ns": now_wall,
                        "source_timestamp_ns": now_mono,
                        "action_sent_json": "{}",
                    },
                )
                recorder.record_row(
                    "robot_feedback",
                    {
                        "sample_id": sample_id,
                        "control_sample_index": index,
                        "host_monotonic_ns": now_mono,
                        "host_wall_time_ns": now_wall,
                        "source_timestamp_ns": now_mono,
                        "observation_json": "{}",
                    },
                )
                recorder.record_row(
                    "vr_input",
                    {
                        "sample_id": sample_id,
                        "control_sample_index": index,
                        "host_monotonic_ns": now_mono,
                        "host_wall_time_ns": now_wall,
                        "source_timestamp_ns": now_mono,
                        "left_state": [],
                        "right_state": [],
                    },
                )
                if index == 0:
                    recorder.record_row(
                        "language_action",
                        {
                            "host_monotonic_ns": now_mono,
                            "host_wall_time_ns": now_wall,
                            "annotation_schema": "piper.vla.language.v1",
                            "primitive": "grasp",
                            "arm": "right",
                            "language_action": "Grasp the test object with the right gripper.",
                            "object": "the test object",
                            "target": "the target",
                            "source": "operator_dashboard",
                        },
                    )
                    recorder.record_mcap(
                        "/system/diagnostics",
                        {"host_monotonic_ns": now_mono, "host_wall_time_ns": now_wall, "cpu": {}},
                    )
                for name in ("cam_front", "cam_left_wrist", "cam_right_wrist"):
                    frame = rng.integers(0, 256, size=(72, 96, 3), dtype=np.uint8)
                    assert recorder.record_camera(name, frame, now_mono)

            episode_path = recorder.finish_episode(task_success=True, failure_reason="none")
            canonical = validate_episode(episode_path)
            assert canonical.valid, canonical.errors
            mcap_path = episode_path / "raw.mcap"
            assert mcap_path.is_file()
            report = validate_mcap(mcap_path)
            assert report.valid, report.errors
            assert report.metrics["message_counts"]["/robot/command"] == 8
            assert report.metrics["message_counts"]["/camera/cam_front/color/compressed"] == 8
            assert report.metrics["calibration_status"] == "usable_with_limitations"
            assert report.metrics["tf_status"] == "usable_with_limitations"
            assert report.metrics["message_counts"]["/tf_static"] == 1
            assert report.metrics["message_counts"]["/annotation/instruction"] == 1
            assert report.metrics["message_counts"]["/annotation/language_action"] == 1
            assert report.metrics["camera_headers"]["/camera/cam_front/color/compressed"]["frame_id"] == "global_camera_link"
            assert report.metrics["sample_lineage_consistent"] is True
            assert report.metrics["sample_lineage"]["/robot/command"][0] == {
                "sample_id": "episode_mcap_test:000000", "control_sample_index": 0,
            }
            camera_header = report.metrics["camera_headers"]["/camera/cam_front/color/compressed"]
            assert camera_header["camera_stream_sequence_id"] == 7
            assert camera_header["camera_host_receive_monotonic_ns"] <= camera_header["camera_enqueue_host_monotonic_ns"] <= camera_header["camera_write_host_monotonic_ns"]
            metadata = json.loads((episode_path / "metadata.json").read_text())
            assert metadata["mcap_log"]["status"] == "complete"
            assert (episode_path / "language_actions.parquet").is_file()
        finally:
            recorder.close()
