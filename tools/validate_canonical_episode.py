#!/usr/bin/env python3
"""Validate the dependency-free semantic invariants of a canonical episode."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

PHASES = {"reset", "approach", "grasp", "lift", "transport", "place", "release", "retreat", "done"}
OUTCOMES = ("grasp_success", "lift_success", "transport_success", "place_success", "release_success")


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def vector(value: Any, length: int, label: str, errors: list[str]) -> None:
    require(isinstance(value, list) and len(value) == length, f"{label}: expected length {length}", errors)
    if isinstance(value, list):
        require(all(isinstance(x, (int, float)) and math.isfinite(x) for x in value), f"{label}: non-finite/non-numeric value", errors)


def pose(value: Any, label: str, errors: list[str]) -> None:
    require(isinstance(value, dict), f"{label}: expected object", errors)
    if not isinstance(value, dict):
        return
    require(bool(value.get("frame")), f"{label}.frame: missing", errors)
    vector(value.get("position_m"), 3, f"{label}.position_m", errors)
    quat = value.get("quaternion_xyzw")
    vector(quat, 4, f"{label}.quaternion_xyzw", errors)
    if isinstance(quat, list) and len(quat) == 4 and all(isinstance(x, (int, float)) for x in quat):
        require(abs(math.sqrt(sum(x * x for x in quat)) - 1.0) <= 1e-3, f"{label}: quaternion is not normalized", errors)


def validate(doc: Any) -> list[str]:
    errors: list[str] = []
    require(isinstance(doc, dict), "root: expected object", errors)
    if not isinstance(doc, dict):
        return errors
    meta, frames = doc.get("metadata"), doc.get("frames")
    require(isinstance(meta, dict), "metadata: expected object", errors)
    require(isinstance(frames, list) and bool(frames), "frames: expected non-empty array", errors)
    if not isinstance(meta, dict) or not isinstance(frames, list) or not frames:
        return errors

    required_meta = ("episode_id", "operator_id", "task_id", "schema_version", "calibration_version", "robot_urdf_version", "teleop_commit", "control_commit", "camera_config", "start_host_monotonic_ns", "end_host_monotonic_ns", "outcomes", "task_success", "failure_reason")
    for key in required_meta:
        require(key in meta, f"metadata.{key}: missing", errors)
    require(meta.get("task_id") == "piper_pick_place_v1", "metadata.task_id: unsupported", errors)
    require(meta.get("schema_version") == "piper_canonical_v1", "metadata.schema_version: unsupported", errors)
    pose(meta.get("object_initial_pose"), "metadata.object_initial_pose", errors)
    pose(meta.get("target_pose"), "metadata.target_pose", errors)

    previous_time = -1
    previous_source = -1
    camera_sources: dict[str, int] = {}
    required_frame = ("host_monotonic_ns", "phase", "source_index", "quest", "tcp_target", "command", "robot_feedback", "gripper", "cameras", "controller_mode", "safety_state")
    for i, frame in enumerate(frames):
        label = f"frames[{i}]"
        require(isinstance(frame, dict), f"{label}: expected object", errors)
        if not isinstance(frame, dict):
            continue
        for key in required_frame:
            require(key in frame, f"{label}.{key}: missing", errors)
        now = frame.get("host_monotonic_ns")
        src = frame.get("source_index")
        require(isinstance(now, int) and now > previous_time, f"{label}.host_monotonic_ns: not strictly increasing", errors)
        require(isinstance(src, int) and src > previous_source, f"{label}.source_index: not strictly increasing", errors)
        if isinstance(now, int): previous_time = now
        if isinstance(src, int): previous_source = src
        require(frame.get("phase") in PHASES, f"{label}.phase: invalid", errors)
        quest = frame.get("quest", {})
        pose(quest.get("pose"), f"{label}.quest.pose", errors)
        require(isinstance(quest.get("device_time_ns"), int), f"{label}.quest.device_time_ns: missing", errors)
        require(isinstance(quest.get("host_receive_ns"), int), f"{label}.quest.host_receive_ns: missing", errors)
        target = frame.get("tcp_target", {})
        pose(target.get("T_base_tcp"), f"{label}.tcp_target.T_base_tcp", errors)
        require(isinstance(target.get("generated_ns"), int), f"{label}.tcp_target.generated_ns: missing", errors)
        command = frame.get("command", {})
        vector(command.get("q_command_rad"), 6, f"{label}.command.q_command_rad", errors)
        require(isinstance(command.get("sent_ns"), int), f"{label}.command.sent_ns: missing", errors)
        feedback = frame.get("robot_feedback", {})
        vector(feedback.get("q_actual_rad"), 6, f"{label}.robot_feedback.q_actual_rad", errors)
        vector(feedback.get("dq_actual_rad_s"), 6, f"{label}.robot_feedback.dq_actual_rad_s", errors)
        pose(feedback.get("T_base_tcp_actual"), f"{label}.robot_feedback.T_base_tcp_actual", errors)
        gripper = frame.get("gripper", {})
        for key in ("command_m", "actual_m"):
            value = gripper.get(key)
            require(isinstance(value, (int, float)) and 0.0 <= value <= 0.087, f"{label}.gripper.{key}: outside [0, 0.087] m", errors)
        cameras = frame.get("cameras")
        require(isinstance(cameras, list) and bool(cameras), f"{label}.cameras: empty", errors)
        if isinstance(cameras, list):
            for camera in cameras:
                name = camera.get("name", "<unnamed>") if isinstance(camera, dict) else "<invalid>"
                require(isinstance(camera, dict), f"{label}.cameras[{name}]: expected object", errors)
                if not isinstance(camera, dict): continue
                cam_src = camera.get("source_index")
                require(isinstance(cam_src, int) and cam_src > camera_sources.get(name, -1), f"{label}.cameras[{name}].source_index: not increasing", errors)
                if isinstance(cam_src, int): camera_sources[name] = cam_src
                require(camera.get("decoded") is True, f"{label}.cameras[{name}]: undecodable", errors)
                require(isinstance(camera.get("width"), int) and camera["width"] > 0, f"{label}.cameras[{name}].width: invalid", errors)
                require(isinstance(camera.get("height"), int) and camera["height"] > 0, f"{label}.cameras[{name}].height: invalid", errors)

    require(frames[-1].get("phase") == "done", "frames: final phase must be done", errors)
    require(isinstance(meta.get("end_host_monotonic_ns"), int) and meta["end_host_monotonic_ns"] >= previous_time, "metadata.end_host_monotonic_ns: before final frame", errors)
    outcomes = meta.get("outcomes", {})
    for key in OUTCOMES:
        require(isinstance(outcomes.get(key), bool), f"metadata.outcomes.{key}: missing/not boolean", errors)
    if meta.get("task_success") is True:
        require(meta.get("failure_reason") == "none", "successful episode must use failure_reason=none", errors)
        require(all(outcomes.get(k) is True for k in OUTCOMES), "successful episode requires all stage outcomes", errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("episode", type=Path)
    args = parser.parse_args()
    with args.episode.open(encoding="utf-8") as stream:
        doc = json.load(stream)
    errors = validate(doc)
    if errors:
        print(f"FAIL: {len(errors)} validation error(s)")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"PASS: {args.episode} ({len(doc['frames'])} frames, schema={doc['metadata']['schema_version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
