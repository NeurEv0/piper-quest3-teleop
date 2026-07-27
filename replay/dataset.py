"""Load replayable joint targets from a processed canonical dataset."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

ACTION_KEYS = (
    "left_joint_1.pos", "left_joint_2.pos", "left_joint_3.pos",
    "left_joint_4.pos", "left_joint_5.pos", "left_joint_6.pos",
    "left_gripper.pos", "right_joint_1.pos", "right_joint_2.pos",
    "right_joint_3.pos", "right_joint_4.pos", "right_joint_5.pos",
    "right_joint_6.pos", "right_gripper.pos",
)
SUPPORTED_ACTION_SPACE = "piper_joint_target_14"
ACTION_SOURCES = {"controller.command_t", "action"}


@dataclass(frozen=True)
class ReplayFrame:
    frame_index: int
    timestamp: float
    target: tuple[float, ...]

    def as_action(self) -> dict[str, float]:
        return dict(zip(ACTION_KEYS, self.target))


@dataclass(frozen=True)
class ReplayTrajectory:
    dataset_root: Path
    episode_index: int
    action_source: str
    fps: float
    frames: tuple[ReplayFrame, ...]

    @property
    def duration_s(self) -> float:
        if len(self.frames) < 2:
            return 0.0
        return self.frames[-1].timestamp - self.frames[0].timestamp


def load_trajectory(
    dataset_root: str | Path,
    episode_index: int,
    *,
    action_source: str = "controller.command_t",
    start_frame: int | None = None,
    end_frame: int | None = None,
) -> ReplayTrajectory:
    root = Path(dataset_root).expanduser().resolve()
    info_path = root / "meta" / "info.json"
    if not info_path.is_file():
        raise FileNotFoundError(f"canonical dataset metadata not found: {info_path}")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    action_space = info.get("action_space") or info.get("config", {}).get("action_space")
    if action_space != SUPPORTED_ACTION_SPACE:
        raise ValueError(
            f"replay requires {SUPPORTED_ACTION_SPACE}, dataset declares {action_space!r}"
        )
    _validate_action_contract(info)
    if action_source not in ACTION_SOURCES:
        raise ValueError(f"unsupported action source: {action_source}")

    episode_path = _episode_path(root, info, episode_index)
    rows = _read_rows(episode_path, action_source)
    selected = [
        row for row in rows
        if (start_frame is None or int(row["frame_index"]) >= start_frame)
        and (end_frame is None or int(row["frame_index"]) <= end_frame)
    ]
    if not selected:
        raise ValueError(f"episode {episode_index} has no frames in the requested range")

    frames: list[ReplayFrame] = []
    previous_timestamp: float | None = None
    for row in selected:
        timestamp = float(row["timestamp"])
        target = tuple(float(value) for value in row[action_source])
        if len(target) != len(ACTION_KEYS):
            raise ValueError(f"frame {row['frame_index']} has {len(target)} actions; expected 14")
        if not math.isfinite(timestamp) or not all(math.isfinite(value) for value in target):
            raise ValueError(f"frame {row['frame_index']} contains a non-finite value")
        if previous_timestamp is not None and timestamp < previous_timestamp:
            raise ValueError("episode timestamps are not monotonic")
        previous_timestamp = timestamp
        frames.append(ReplayFrame(int(row["frame_index"]), timestamp, target))

    return ReplayTrajectory(
        dataset_root=root,
        episode_index=episode_index,
        action_source=action_source,
        fps=float(info.get("fps") or info.get("config", {}).get("fps") or 0.0),
        frames=tuple(frames),
    )


def _validate_action_contract(info: dict[str, Any]) -> None:
    feature = info.get("features", {}).get("action", {})
    shape = feature.get("shape")
    if shape and list(shape) != [14]:
        raise ValueError(f"dataset action shape is {shape!r}; expected [14]")
    names = feature.get("names")
    if names and len(names) != 14:
        raise ValueError(f"dataset declares {len(names)} action names; expected 14")


def _episode_path(root: Path, info: dict[str, Any], episode_index: int) -> Path:
    template = info.get(
        "data_path", "data/chunk-{chunk_index:03d}/episode_{episode_index:06d}.parquet"
    )
    chunk_size = int(info.get("config", {}).get("chunk_size", 1000))
    relative = template.format(
        episode_index=episode_index,
        chunk_index=episode_index // max(1, chunk_size),
    )
    path = root / relative
    if not path.is_file():
        raise FileNotFoundError(f"episode parquet not found: {path}")
    return path


def _read_rows(path: Path, action_column: str) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required to replay canonical Parquet data") from exc
    return pq.read_table(
        path, columns=["frame_index", "timestamp", action_column]
    ).to_pylist()
