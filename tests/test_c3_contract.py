from copy import deepcopy

from canonical_raw.action_state import ACTION_KEYS, typed_control_fields, typed_feedback_fields
from canonical_raw.c3_validator import validate_c3_rows
from canonical_raw.contract import collection_profile, default_action_space_contract


def fixture():
    action = {key: 0.0 for key in ACTION_KEYS}
    action["left_gripper.pos"] = action["right_gripper.pos"] = 0.04
    metadata = {
        "action_space": default_action_space_contract(),
        "collection_profile": collection_profile(
            record_action_from_follower=True, teleop_joint_alpha=1.0, teleop_gripper_alpha=1.0
        ),
    }
    control = {"sample_id": "sample:0", **typed_control_fields(action, action)}
    feedback = {"sample_id": "sample:0", **typed_feedback_fields(action, action)}
    return metadata, [control], [feedback]


def test_c3_contract_and_lineage_pass():
    metadata, control, feedback = fixture()
    report = validate_c3_rows(metadata, control, feedback)
    assert report["valid"]
    assert report["dimension"] == 14


def test_c3_dimension_order_unit_and_range_faults():
    metadata, control, feedback = fixture()
    metadata["action_space"]["ordered_dimensions"][0]["unit"] = "degree"
    assert "action_space.order_or_semantics_mismatch" in validate_c3_rows(metadata, control, feedback)["errors"]

    metadata, control, feedback = fixture()
    control[0]["action_sent_vector"][0] = 99.0
    assert any(code.startswith("action.range_violation") for code in validate_c3_rows(metadata, control, feedback)["errors"])

    metadata, control, feedback = fixture()
    control[0]["action_sent_vector"].pop()
    assert any(code.startswith("action_space.vector_invalid") for code in validate_c3_rows(metadata, control, feedback)["errors"])


def test_c3_missing_capability_failure_and_lineage_faults():
    metadata, control, feedback = fixture()
    del metadata["collection_profile"]
    assert "collection_profile.missing" in validate_c3_rows(metadata, control, feedback)["errors"]

    metadata, control, feedback = fixture()
    control[0]["control_processing"]["ik"] = {"status": "fail"}
    assert any(code.startswith("control_processing.reason_missing") for code in validate_c3_rows(metadata, control, feedback)["errors"])

    metadata, control, feedback = fixture()
    assert any(code.startswith("lineage.measured_missing") for code in validate_c3_rows(metadata, control, [])["errors"])

    metadata, control, feedback = fixture()
    del feedback[0]["gripper_measured_state"]
    assert any(code.startswith("lineage.feedback_field_missing") for code in validate_c3_rows(metadata, control, feedback)["errors"])
