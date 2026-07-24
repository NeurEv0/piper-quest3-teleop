"""Offline C3 action/state contract validation."""
from __future__ import annotations
import math
from .contract import ACTION_SPACE_VERSION, default_action_space_contract

REQUIRED_CONTROL = ("action_requested_vector", "action_sent_vector", "joint_command_requested", "joint_command_sent", "gripper_target", "gripper_command_sent", "control_processing")
REQUIRED_FEEDBACK = ("joint_state", "gripper_measured_state", "command_error", "safety_state_typed")

def validate_c3_rows(metadata: dict[str, object], control_rows: list[dict[str, object]], feedback_rows: list[dict[str, object]]) -> dict[str, object]:
    errors: list[str] = []
    contract = metadata.get("action_space")
    if not isinstance(contract, dict) or contract.get("version") != ACTION_SPACE_VERSION:
        errors.append("action_space.version_mismatch")
        return {"valid": False, "errors": errors}
    dimensions = contract.get("ordered_dimensions")
    frozen = default_action_space_contract()["ordered_dimensions"]
    if contract.get("dimension") != 14 or not isinstance(dimensions, list) or len(dimensions) != 14:
        errors.append("action_space.dimension_invalid")
        return {"valid": False, "errors": errors}
    for actual, expected in zip(dimensions, frozen):
        if actual != expected:
            errors.append("action_space.order_or_semantics_mismatch")
            break
    if not isinstance(metadata.get("collection_profile"), dict):
        errors.append("collection_profile.missing")
    feedback = {row.get("sample_id"): row for row in feedback_rows}
    for index, row in enumerate(control_rows):
        for field in REQUIRED_CONTROL:
            if row.get(field) is None:
                errors.append(f"lineage.control_field_missing:{index}:{field}")
        for vector_name in ("action_requested_vector", "action_sent_vector"):
            vector = row.get(vector_name)
            if not isinstance(vector, list) or len(vector) != 14 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
                errors.append(f"action_space.vector_invalid:{index}:{vector_name}")
                continue
            for dimension, value in zip(dimensions, vector):
                limits = dimension["command_limits"]
                if value < limits["min"] or value > limits["max"]:
                    errors.append(f"action.range_violation:{index}:{dimension['name']}")
        processing = row.get("control_processing")
        if isinstance(processing, dict):
            for stage in ("ik", "clamp", "safety"):
                result = processing.get(stage)
                if not isinstance(result, dict) or not result.get("status") or (result.get("status") in ("fail", "unavailable") and not result.get("reason_code")):
                    errors.append(f"control_processing.reason_missing:{index}:{stage}")
        measured = feedback.get(row.get("sample_id"))
        if measured is None:
            errors.append(f"lineage.measured_missing:{index}")
            continue
        for field in REQUIRED_FEEDBACK:
            if measured.get(field) is None:
                errors.append(f"lineage.feedback_field_missing:{index}:{field}")
    return {"valid": not errors, "errors": errors, "dimension": 14, "sample_count": len(control_rows)}
