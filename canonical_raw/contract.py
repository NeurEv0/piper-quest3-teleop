"""Versioned Canonical Raw capture-side contract constants."""

from __future__ import annotations


CAPTURE_CONTRACT_VERSION = "piper_capture_cleaning_ready_v1"
ACTION_SEMANTICS_VERSION = "piper_action_semantics_v1"
ACTION_SPACE_VERSION = "piper_bimanual_joint_position_v1"
ACTION_CONVERSION_VERSION = "lerobot_feature_dict_to_si_v1"
CONTROL_PROCESSING_VERSION = "piper_vr_ik_pipeline_v1"
COLLECTION_PROFILE_VERSION = "piper_bimanual_quest3_cleaning_ready_v1"
SESSION_EVENT_SCHEMA_VERSION = "piper_session_event_v1"

RECORDING_STATES = ("inprogress", "finalized", "aborted", "incomplete")
TERMINATION_REASONS = (
    "operator_success",
    "operator_failure",
    "operator_abort",
    "operator_reset",
    "task_change",
    "time_gap",
    "process_interruption",
    "process_shutdown",
    "writer_error",
    "session_log_ended",
    "signal",
    "task_failed",
    "operator_marked_failure",
    "operator_stop",
    "unknown",
)
TIMESTAMP_UNAVAILABLE_REASONS = (
    "hardware_timestamp_unavailable",
    "quest_device_timestamp_unavailable",
    "camera_sdk_timestamp_unavailable",
    "sdk_unavailable",
    "no_controller_event",
    "source_unavailable",
    "legacy_missing",
    "synthetic_fixture",
    "not_recorded",
)
CALIBRATION_STATUSES = ("missing", "usable_with_limitations", "usable", "calibrated", "verified", "valid")

PRIMARY_TIMEBASE = "host_monotonic_ns"
WALL_TIMEBASE = "host_wall_time_ns"

SAMPLE_SYNC_STREAMS = ("control", "robot_feedback", "vr_input")
CORE_ROW_FIELDS = (
    "sample_id",
    "control_sample_index",
    "row_sequence_id",
    "host_monotonic_ns",
    "host_wall_time_ns",
    "source_timestamp_ns",
)

# Capture-side C2 quality gates. Reports include these values so downstream
# decisions remain reproducible when defaults evolve in a later contract.
C2_VALIDATION_THRESHOLDS = {
    "sample_coverage_min": 1.0,
    "control_rate_min_ratio": 0.90,
    "control_rate_max_ratio": 1.10,
    "control_jitter_p95_ms_max": 8.0,
    "control_max_gap_ms": 100.0,
    "quest_stale_age_p95_ms_max": 100.0,
    "robot_feedback_stale_p95_ms_max": 100.0,
    "camera_frame_count_skew_max": 1,
    "camera_write_latency_p95_ms_max": 100.0,
    "multicamera_sync_ms_max": 40.0,
}

LIFECYCLE_ROW_FIELDS = {
    "control": (
        "action_request_generated_host_monotonic_ns", "action_send_start_host_monotonic_ns",
        "action_send_end_host_monotonic_ns", "action_send_result_received_host_monotonic_ns",
    ),
    "robot_feedback": (
        "robot_feedback_source_timestamp_ns", "robot_feedback_source_timestamp_unavailable_reason",
        "robot_feedback_read_start_host_monotonic_ns", "robot_feedback_host_receive_monotonic_ns",
        "robot_feedback_enqueue_host_monotonic_ns",
    ),
    "vr_input": (
        "controller_event_source_timestamp_ns", "controller_event_source_timestamp_unavailable_reason",
        "controller_event_host_receive_monotonic_ns", "controller_event_host_receive_unavailable_reason",
        "controller_event_enqueue_host_monotonic_ns",
        "controller_event_age_s", "controller_event_count",
    ),
    "camera": (
        "camera_sensor_timestamp_ns", "camera_sensor_timestamp_unavailable_reason",
        "camera_host_receive_monotonic_ns", "camera_enqueue_host_monotonic_ns",
        "camera_write_host_monotonic_ns", "camera_stream_sequence_id",
    ),
}

SESSION_EVENT_TYPES = frozenset(
    {
        "session_start", "operator_start", "operator_stop", "success", "failure",
        "operator_abort", "reset", "task_change", "hardware_block", "dashboard_command",
        "camera_mode_change", "process_shutdown", "process_interruption",
        "cleaning_ready_gate_passed", "cleaning_ready_gate_failed",
    }
)


_JOINT_LIMITS_RAD = ((-2.618, 2.618), (0.0, 3.14), (-2.697, 0.0), (-1.832, 1.832), (-1.22, 1.22), (-3.14, 3.14))

def default_action_space_contract() -> dict[str, object]:
    dimensions: list[dict[str, object]] = []
    for side in ("left", "right"):
        for joint_index, limits in enumerate(_JOINT_LIMITS_RAD, start=1):
            source = f"{side}_joint_{joint_index}.pos"
            dimensions.append({"index": len(dimensions), "name": source.removesuffix(".pos"), "quantity": "joint_position", "unit": "rad", "frame_id": f"{side}_arm_base", "source_field_path": f"control.action_requested.{source}", "sent_field_path": f"control.action_sent.{source}", "measured_field_path": f"robot_feedback.observation.{source}", "command_limits": {"min": limits[0], "max": limits[1]}})
        source = f"{side}_gripper.pos"
        dimensions.append({"index": len(dimensions), "name": f"{side}_gripper_opening", "quantity": "gripper_opening", "unit": "m", "frame_id": f"{side}_gripper", "source_field_path": f"control.action_requested.{source}", "sent_field_path": f"control.action_sent.{source}", "measured_field_path": f"robot_feedback.observation.{source}", "command_limits": {"min": 0.0, "max": 0.07}})
    return {"version": ACTION_SPACE_VERSION, "conversion_version": ACTION_CONVERSION_VERSION, "robot_configuration": "dual_piper_6dof_gripper", "dimension": 14, "ordered_dimensions": dimensions}

def collection_profile(*, record_action_from_follower: bool, teleop_joint_alpha: float, teleop_gripper_alpha: float) -> dict[str, object]:
    return {"version": COLLECTION_PROFILE_VERSION, "record_action_from_follower": bool(record_action_from_follower), "teleop_joint_alpha": float(teleop_joint_alpha), "teleop_gripper_alpha": float(teleop_gripper_alpha), "max_joint_delta_rad_per_sample": 0.02, "max_gripper_delta_m_per_sample": 0.002, "safety_clamp_behavior": "delegate_to_robot_adapter_and_record_result", "hardware_velocity_limits": {"status": "unavailable", "reason": "not_frozen_without_hardware_validation"}}

def session_event(
    event_type: str,
    *,
    episode_id: str | None = None,
    reason: str | None = None,
    source: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the stable session event envelope; timestamps are writer-owned."""
    if event_type not in SESSION_EVENT_TYPES:
        raise ValueError(f"unsupported session event type: {event_type}")
    if not source:
        raise ValueError("session event source is required")
    return {
        "schema_version": SESSION_EVENT_SCHEMA_VERSION,
        "event_type": event_type,
        "episode_id": episode_id,
        "reason": reason,
        "source": source,
        "payload": dict(payload or {}),
    }


def default_timebase_contract() -> dict[str, object]:
    return {
        "primary": PRIMARY_TIMEBASE,
        "wall": WALL_TIMEBASE,
        "unit": "nanosecond",
        "clock": "python.time",
        "monotonic_source": "time.monotonic_ns",
        "wall_source": "time.time_ns",
    }


def default_action_semantics() -> dict[str, object]:
    return {
        "version": ACTION_SEMANTICS_VERSION,
        "teleop.intent_t": "vr_controller_pose_and_buttons",
        "controller.command_t": "robot.send_action input/output",
        "robot.executed_t": "robot encoder feedback from robot_feedback stream",
        "policy.action_t": "derived by data_postprocess from the selected action_space",
        "record_action_from_follower": "unknown",
    }
