"""Versioned topic and payload contract for Piper MCAP logs."""

from __future__ import annotations

import json


PROFILE = "piper.quest3.mcap.v1"
LIBRARY = "piper-quest3-canonical-raw"
JSON_SCHEMA_NAME = "piper.log.JsonMessage.v1"
IMAGE_SCHEMA_NAME = "piper.log.CompressedImage.v1"

ROW_TOPICS = {
    "control": "/robot/command",
    "robot_feedback": "/robot/state",
    "vr_input": "/teleop/quest3/state",
    "event": "/episode/event",
    "language_action": "/annotation/language_action",
}

LANGUAGE_INSTRUCTION_TOPIC = "/annotation/instruction"

CAMERA_TOPIC_TEMPLATE = "/camera/{camera_name}/color/compressed"
SYSTEM_DIAGNOSTICS_TOPIC = "/system/diagnostics"
EPISODE_METADATA_TOPIC = "/episode/metadata"
CAPABILITIES_TOPIC = "/system/capabilities"
CALIBRATION_TOPIC = "/calibration/snapshot"
TF_STATUS_TOPIC = "/tf_static/status"
TF_TOPIC = "/tf_static"

REQUIRED_TOPICS = (
    "/episode/event",
    "/episode/metadata",
    "/robot/command",
    "/robot/state",
    "/teleop/quest3/state",
    "/system/capabilities",
)

CAMERA_NAMES = ("cam_front", "cam_left_wrist", "cam_right_wrist")

JSON_SCHEMA = json.dumps(
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": JSON_SCHEMA_NAME,
        "type": "object",
        "required": ["host_monotonic_ns", "host_wall_time_ns", "sequence_id"],
        "properties": {
            "host_monotonic_ns": {"type": "integer", "minimum": 0},
            "host_wall_time_ns": {"type": "integer", "minimum": 0},
            "source_timestamp_ns": {"type": "integer", "minimum": 0},
            "sequence_id": {"type": "integer", "minimum": 0},
            "frame_id": {"type": "string"},
        },
        "additionalProperties": True,
    },
    separators=(",", ":"),
).encode("utf-8")

IMAGE_HEADER_SCHEMA = json.dumps(
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": IMAGE_SCHEMA_NAME,
        "description": "PIMG v1 envelope: magic[4], version<u16>, header_bytes<u32>, JSON header, JPEG bytes",
        "type": "object",
        "required": [
            "camera_name",
            "frame_id",
            "host_monotonic_ns",
            "host_wall_time_ns",
            "source_timestamp_ns",
            "sequence_id",
            "width",
            "height",
            "pixel_encoding",
            "compression",
        ],
        "additionalProperties": True,
    },
    separators=(",", ":"),
).encode("utf-8")


def default_capabilities(camera_mode: str = "mosaic") -> dict[str, object]:
    return {
        "contract_version": PROFILE,
        "rgb_cameras": {
            "status": "enabled" if camera_mode == "mosaic" else "intentionally_disabled",
            "streams": list(CAMERA_NAMES),
            "encoding": "jpeg",
        },
        "depth": {"status": "not_enabled", "reason": "Orbbec color-only runtime configuration"},
        "point_cloud": {"status": "derived_offline", "source": "depth + camera_info + tf"},
        "lidar": {"status": "not_present"},
        "imu": {"status": "not_present"},
        "odometry": {"status": "not_present", "reason": "fixed-base dual-arm rig"},
        "joint_state": {"status": "enabled", "topic": "/robot/state"},
        "end_effector_state": {"status": "planned", "reason": "typed FK/IK state not emitted yet"},
        "teleoperation_input": {"status": "enabled", "topic": "/teleop/quest3/state"},
        "robot_command": {"status": "enabled", "topic": "/robot/command"},
        "language_instruction": {"status": "enabled", "topic": LANGUAGE_INSTRUCTION_TOPIC},
        "language_action": {"status": "enabled", "topic": "/annotation/language_action"},
        "system_diagnostics": {"status": "enabled", "topic": SYSTEM_DIAGNOSTICS_TOPIC},
        "tf": {"status": "requires_valid_calibration", "topic": TF_TOPIC},
    }
