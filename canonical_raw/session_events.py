"""Deterministic reconstruction of episode boundaries from session events."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class EpisodeBoundary:
    episode_id: str
    start_host_monotonic_ns: int
    end_host_monotonic_ns: int
    termination_reason: str
    recording_state: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reconstruct_episode_boundaries(events: list[dict[str, Any]]) -> list[EpisodeBoundary]:
    ordered = sorted(events, key=lambda row: (int(row["host_monotonic_ns"]), int(row.get("session_event_sequence_id", 0))))
    active: dict[str, dict[str, Any]] = {}
    boundaries: list[EpisodeBoundary] = []
    terminal = {
        "success": "finalized", "failure": "finalized", "operator_abort": "aborted",
        "process_interruption": "incomplete", "reset": "aborted", "task_change": "aborted",
    }
    for row in ordered:
        event_type = str(row.get("event_type") or row.get("event"))
        episode_id = row.get("episode_id")
        stamp = int(row["host_monotonic_ns"])
        if event_type == "operator_start" and episode_id:
            if episode_id in active:
                raise ValueError(f"duplicate start for episode {episode_id}")
            active[str(episode_id)] = row
        elif event_type in terminal and episode_id in active:
            start = active.pop(str(episode_id))
            boundaries.append(EpisodeBoundary(
                episode_id=str(episode_id),
                start_host_monotonic_ns=int(start["host_monotonic_ns"]),
                end_host_monotonic_ns=stamp,
                termination_reason=str(row.get("reason") or event_type),
                recording_state=terminal[event_type],
            ))
    for episode_id, start in active.items():
        boundaries.append(EpisodeBoundary(
            episode_id=episode_id,
            start_host_monotonic_ns=int(start["host_monotonic_ns"]),
            end_host_monotonic_ns=int(ordered[-1]["host_monotonic_ns"]),
            termination_reason="session_log_ended",
            recording_state="incomplete",
        ))
    boundaries.sort(key=lambda boundary: boundary.start_host_monotonic_ns)
    if any(left.end_host_monotonic_ns > right.start_host_monotonic_ns for left, right in zip(boundaries, boundaries[1:])):
        raise ValueError("reconstructed episode boundaries overlap")
    return boundaries
