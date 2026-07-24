"""Typed C3 action/state rows and requested/sent/measured lineage."""

from __future__ import annotations

from typing import Any

from .contract import ACTION_CONVERSION_VERSION, ACTION_SPACE_VERSION, CONTROL_PROCESSING_VERSION

JOINT_KEYS = tuple(f"{side}_joint_{index}.pos" for side in ("left", "right") for index in range(1, 7))
GRIPPER_KEYS = tuple(f"{side}_gripper.pos" for side in ("left", "right"))
ACTION_KEYS = tuple(
    key for side in ("left", "right")
    for key in (*[f"{side}_joint_{index}.pos" for index in range(1, 7)], f"{side}_gripper.pos")
)


def _values(payload: dict[str, Any], keys: tuple[str, ...]) -> list[float]:
    return [float(payload[key]) for key in keys]


def typed_control_fields(
    requested: dict[str, Any], sent: dict[str, Any], processing: dict[str, Any] | None = None
) -> dict[str, Any]:
    processing = processing or {}
    return {
        "action_space_version": ACTION_SPACE_VERSION,
        "action_conversion_version": ACTION_CONVERSION_VERSION,
        "action_requested_vector": _values(requested, ACTION_KEYS),
        "action_sent_vector": _values(sent, ACTION_KEYS),
        "joint_command_requested": {"names": list(JOINT_KEYS), "values_rad": _values(requested, JOINT_KEYS)},
        "joint_command_sent": {"names": list(JOINT_KEYS), "values_rad": _values(sent, JOINT_KEYS)},
        "gripper_target": {"names": list(GRIPPER_KEYS), "values_m": _values(requested, GRIPPER_KEYS)},
        "gripper_command_sent": {"names": list(GRIPPER_KEYS), "values_m": _values(sent, GRIPPER_KEYS)},
        "control_processing": {
            "version": CONTROL_PROCESSING_VERSION,
            "ik": dict(processing.get("ik") or {"status": "unavailable", "reason_code": "ik_diagnostics_unavailable"}),
            "smoothing_delta_rad": list(processing.get("smoothing_delta_rad") or []),
            "clamp": dict(processing.get("clamp") or {"status": "unavailable", "reason_code": "robot_adapter_clamp_status_unavailable"}),
            "safety": dict(processing.get("safety") or {"status": "unavailable", "reason_code": "robot_adapter_safety_result_unavailable"}),
        },
    }


def typed_feedback_fields(observation: dict[str, Any], sent: dict[str, Any]) -> dict[str, Any]:
    measured_joints = _values(observation, JOINT_KEYS)
    sent_joints = _values(sent, JOINT_KEYS)
    measured_grippers = _values(observation, GRIPPER_KEYS)
    sent_grippers = _values(sent, GRIPPER_KEYS)
    return {
        "state_conversion_version": ACTION_CONVERSION_VERSION,
        "joint_state": {
            "names": list(JOINT_KEYS),
            "position_rad": measured_joints,
            "velocity_rad_s": [],
            "velocity_status": "unavailable",
            "velocity_unavailable_reason": "adapter_exposes_position_only",
        },
        "gripper_measured_state": {"names": list(GRIPPER_KEYS), "values_m": measured_grippers},
        "command_error": {
            "definition": "sent_minus_measured_at_paired_pre_send_feedback_sample",
            "joint_position_rad": [command - state for command, state in zip(sent_joints, measured_joints)],
            "gripper_opening_m": [command - state for command, state in zip(sent_grippers, measured_grippers)],
        },
        "safety_state_typed": {
            "status": "normal",
            "source": "capture_loop",
            "hardware_status": "unavailable",
            "hardware_unavailable_reason": "adapter_does_not_expose_typed_safety_state",
        },
    }
