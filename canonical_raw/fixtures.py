"""No-hardware deterministic C1 session boundary fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import session_event


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
        (session_dir / "session.json").write_text(json.dumps({"session_id": name, "fixture": True}, indent=2) + "\n", encoding="utf-8")
        paths.append(session_dir)
    return paths
