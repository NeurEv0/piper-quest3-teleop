from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

from canonical_raw.fixtures import write_cleaning_ready_fixture
from canonical_raw.validator import validate_episode

try:  # pragma: no cover - optional in some environments
    from jsonschema import Draft202012Validator
except Exception:  # pragma: no cover - optional dependency
    Draft202012Validator = None


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_ROOT = REPO_ROOT / "schema"


def _load_schema(name: str) -> dict[str, object]:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def _validate(instance: object, schema_name: str) -> None:
    schema = _load_schema(schema_name)
    if Draft202012Validator is None:
        assert schema["type"] in {"object", "array"}
        if schema_name == "canonical_raw_metadata.schema.json":
            assert schema["properties"]["capture_contract_version"]["const"] == "piper_capture_cleaning_ready_v1"
            assert "calibration" in schema["required"]
        elif schema_name == "canonical_raw_rows.schema.json":
            assert set(schema["required"]) == {"control", "robot_feedback", "vr_input", "camera_timestamps", "events"}
        elif schema_name == "canonical_raw_calibration_snapshot.schema.json":
            assert "transforms" in schema["required"]
        elif schema_name == "canonical_raw_manifest.schema.json":
            assert "files" in schema["required"]
        return
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda item: item.path)
    assert not errors, [error.message for error in errors]


def test_cleaning_ready_fixture_round_trip_and_schemas() -> None:
    with tempfile.TemporaryDirectory(prefix="cleaning_ready_fixture_") as temp:
        session_dir, episode_dir = write_cleaning_ready_fixture(Path(temp))
        assert session_dir.is_dir()
        assert episode_dir.is_dir()

        report = validate_episode(episode_dir)
        assert report.valid, report.errors

        metadata = json.loads((episode_dir / "metadata.json").read_text(encoding="utf-8"))
        manifest = json.loads((episode_dir / "manifest.json").read_text(encoding="utf-8"))
        calibration = json.loads((episode_dir / "calibration_snapshot.json").read_text(encoding="utf-8"))
        validation = json.loads((episode_dir / "validation.json").read_text(encoding="utf-8"))
        rows = {
            "control": pq.read_table(episode_dir / "control.parquet").to_pylist(),
            "robot_feedback": pq.read_table(episode_dir / "robot_feedback.parquet").to_pylist(),
            "vr_input": pq.read_table(episode_dir / "vr_input.parquet").to_pylist(),
            "camera_timestamps": pq.read_table(episode_dir / "camera_timestamps.parquet").to_pylist(),
            "events": pq.read_table(episode_dir / "events.parquet").to_pylist(),
        }

        _validate(metadata, "canonical_raw_metadata.schema.json")
        _validate(
            {
                "control": rows["control"],
                "robot_feedback": rows["robot_feedback"],
                "vr_input": rows["vr_input"],
                "camera_timestamps": rows["camera_timestamps"],
                "events": rows["events"],
            },
            "canonical_raw_rows.schema.json",
        )
        _validate(calibration, "canonical_raw_calibration_snapshot.schema.json")
        _validate(manifest, "canonical_raw_manifest.schema.json")

        assert validation["valid"] is True
        assert metadata["cleaning_ready"] is True
        assert manifest["stream_counts"]["camera"] == 36
