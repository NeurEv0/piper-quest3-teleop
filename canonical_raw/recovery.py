"""Read-only inspection of interrupted Canonical Raw episode directories."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InterruptedEpisode:
    path: str
    session_id: str | None
    episode_id: str
    recording_state: str
    reason: str
    start_host_monotonic_ns: int | None
    observed_files: tuple[str, ...]
    partial_stream_rows: dict[str, int]
    diagnostics: tuple[str, ...]
    training_ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_inprogress(output_root: Path) -> dict[str, Any]:
    """Report and preserve every .inprogress directory without finalizing it."""
    root = Path(output_root)
    episodes = [_inspect_one(path) for path in sorted(root.glob("session_*/*.inprogress"))]
    return {
        "schema_version": "piper_inprogress_diagnostic_v1",
        "generated_wall_time_ns": time.time_ns(),
        "output_root": str(root),
        "interrupted_episode_count": len(episodes),
        "training_ready_count": 0,
        "episodes": [episode.to_dict() for episode in episodes],
        "data_postprocess": {
            "routing": "quarantine",
            "accepted_recording_state": "incomplete",
            "source_immutability": "preserved",
        },
    }


def write_diagnostic_report(output_root: Path, destination: Path) -> dict[str, Any]:
    report = inspect_inprogress(output_root)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _inspect_one(path: Path) -> InterruptedEpisode:
    diagnostics: list[str] = []
    metadata_path = path / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        metadata = {}
        diagnostics.append(f"metadata_unreadable:{type(exc).__name__}")
    state = str(metadata.get("recording_state") or "inprogress")
    if state not in {"incomplete", "aborted"}:
        state = "incomplete"
        diagnostics.append("recording_state_requires_incomplete_routing")
    counts: dict[str, int] = {}
    for stream in sorted(path.glob("*.jsonl.inprogress")):
        try:
            counts[stream.name] = sum(1 for line in stream.open(encoding="utf-8") if line.strip())
        except OSError:
            counts[stream.name] = 0
            diagnostics.append(f"stream_unreadable:{stream.name}")
    return InterruptedEpisode(
        path=str(path),
        session_id=metadata.get("session_id"),
        episode_id=str(metadata.get("episode_id") or path.name.removesuffix(".inprogress")),
        recording_state=state,
        reason=str(metadata.get("termination_reason") or "process_interruption"),
        start_host_monotonic_ns=metadata.get("episode_start_host_monotonic_ns"),
        observed_files=tuple(item.name for item in sorted(path.iterdir()) if item.is_file()),
        partial_stream_rows=counts,
        diagnostics=tuple(diagnostics),
    )
