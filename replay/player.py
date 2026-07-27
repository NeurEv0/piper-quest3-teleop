"""Timestamp-driven trajectory playback without trajectory modification."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Protocol

from .dataset import ReplayTrajectory


class ActionRobot(Protocol):
    def send_action(self, action: dict[str, float]) -> dict[str, float]: ...


@dataclass(frozen=True)
class PlaybackResult:
    episode_index: int
    frames_sent: int
    first_frame: int | None
    last_frame: int | None
    trajectory_duration_s: float
    wall_duration_s: float
    stopped: bool


class TrajectoryPlayer:
    def __init__(
        self,
        robot: ActionRobot,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.robot = robot
        self._clock = clock
        self._sleep = sleep

    def play(
        self,
        trajectory: ReplayTrajectory,
        *,
        speed: float = 1.0,
        stop_event: threading.Event | None = None,
        on_frame: Callable[[int, int], None] | None = None,
    ) -> PlaybackResult:
        if not 0.0 < speed <= 10.0:
            raise ValueError("speed must be greater than 0 and at most 10")
        frames = trajectory.frames
        if not frames:
            raise ValueError("trajectory is empty")

        event = stop_event or threading.Event()
        wall_started = self._clock()
        source_started = frames[0].timestamp
        sent = 0
        last_frame: int | None = None
        for frame in frames:
            if event.is_set():
                break
            deadline = wall_started + (frame.timestamp - source_started) / speed
            self._wait_until(deadline, event)
            if event.is_set():
                break
            self.robot.send_action(frame.as_action())
            sent += 1
            last_frame = frame.frame_index
            if on_frame is not None:
                on_frame(sent, frame.frame_index)

        return PlaybackResult(
            episode_index=trajectory.episode_index,
            frames_sent=sent,
            first_frame=frames[0].frame_index if sent else None,
            last_frame=last_frame,
            trajectory_duration_s=trajectory.duration_s,
            wall_duration_s=self._clock() - wall_started,
            stopped=sent < len(frames),
        )

    def _wait_until(self, deadline: float, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            remaining = deadline - self._clock()
            if remaining <= 0.0:
                return
            self._sleep(min(remaining, 0.02))
