from __future__ import annotations

import json
from pathlib import Path
import threading

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from replay.dataset import ACTION_KEYS, ReplayFrame, ReplayTrajectory, load_trajectory
from replay.player import TrajectoryPlayer


class FakeClock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, duration: float) -> None:
        self.now += duration


class FakeRobot:
    def __init__(self) -> None:
        self.actions: list[dict[str, float]] = []

    def send_action(self, action: dict[str, float]) -> dict[str, float]:
        self.actions.append(action)
        return action


def make_dataset(root: Path, *, action_space: str = "piper_joint_target_14") -> Path:
    canonical = root / "canonical"
    (canonical / "meta").mkdir(parents=True)
    data_dir = canonical / "data" / "chunk-000"
    data_dir.mkdir(parents=True)
    info = {
        "action_space": action_space,
        "fps": 10,
        "data_path": "data/chunk-{chunk_index:03d}/episode_{episode_index:06d}.parquet",
        "config": {"chunk_size": 1000, "fps": 10},
        "features": {"action": {"shape": [14], "names": list(ACTION_KEYS)}},
    }
    (canonical / "meta" / "info.json").write_text(json.dumps(info), encoding="utf-8")
    rows = [
        {
            "frame_index": index,
            "timestamp": timestamp,
            "action": [float(index + 100)] * 14,
            "controller.command_t": [float(index)] * 14,
        }
        for index, timestamp in enumerate((2.0, 2.1, 2.3))
    ]
    pq.write_table(pa.Table.from_pylist(rows), data_dir / "episode_000003.parquet")
    return canonical


def test_loads_controller_commands_and_frame_range(tmp_path: Path) -> None:
    trajectory = load_trajectory(make_dataset(tmp_path), 3, start_frame=1, end_frame=2)
    assert trajectory.action_source == "controller.command_t"
    assert trajectory.fps == 10
    assert [frame.frame_index for frame in trajectory.frames] == [1, 2]
    assert trajectory.frames[0].target == (1.0,) * 14
    assert trajectory.duration_s == pytest.approx(0.2)


def test_can_explicitly_replay_action_column(tmp_path: Path) -> None:
    trajectory = load_trajectory(make_dataset(tmp_path), 3, action_source="action")
    assert trajectory.frames[2].target == (102.0,) * 14


def test_rejects_non_joint_target_dataset(tmp_path: Path) -> None:
    root = make_dataset(tmp_path, action_space="piper_ee_delta_6d_gripper")
    with pytest.raises(ValueError, match="piper_joint_target_14"):
        load_trajectory(root, 3)


def test_player_preserves_targets_and_timestamp_timing() -> None:
    frames = tuple(
        ReplayFrame(index, timestamp, (float(index),) * 14)
        for index, timestamp in enumerate((5.0, 5.1, 5.3))
    )
    trajectory = ReplayTrajectory(Path("/tmp/dataset"), 7, "action", 10.0, frames)
    robot = FakeRobot()
    clock = FakeClock()
    result = TrajectoryPlayer(robot, clock=clock, sleep=clock.sleep).play(
        trajectory, speed=2.0
    )
    assert len(robot.actions) == 3
    assert list(robot.actions[2]) == list(ACTION_KEYS)
    assert tuple(robot.actions[2].values()) == (2.0,) * 14
    assert result.frames_sent == 3
    assert result.wall_duration_s == pytest.approx(0.15)
    assert not result.stopped


def test_player_obeys_pre_set_stop_event() -> None:
    trajectory = ReplayTrajectory(
        Path("/tmp/dataset"), 1, "action", 10.0,
        (ReplayFrame(0, 0.0, (0.0,) * 14),),
    )
    event = threading.Event()
    event.set()
    result = TrajectoryPlayer(FakeRobot()).play(trajectory, stop_event=event)
    assert result.frames_sent == 0
    assert result.stopped
