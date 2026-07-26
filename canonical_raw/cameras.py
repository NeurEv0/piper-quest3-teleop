"""Threaded camera capture with health checks and non-blocking callbacks."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

import numpy as np


@dataclass
class CameraHealth:
    connected: bool = False
    frame_count: int = 0
    last_frame_ns: int = 0
    first_frame_ns: int = 0
    read_errors: int = 0
    green_frames: int = 0
    frozen_frames: int = 0
    width: int = 0
    height: int = 0
    error: str | None = None
    last_signature: float | None = None


def _is_green_frame(frame: np.ndarray) -> bool:
    sample = np.asarray(frame)[::16, ::16]
    if sample.ndim != 3 or sample.shape[2] != 3:
        return True
    means = sample.reshape(-1, 3).mean(axis=0)
    variance = float(sample.var())
    return bool(means[1] > 1.8 * max(means[0], means[2], 1.0) and variance < 2500.0)


class CameraCaptureThread:
    def __init__(
        self,
        name: str,
        camera: Any,
        on_frame: Callable[[str, np.ndarray, int], None],
    ):
        self.name = name
        self.camera = camera
        self.on_frame = on_frame
        self.health = CameraHealth()
        self._latest: np.ndarray | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"camera-{name}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            self.camera.connect()
            self.health.connected = True
            while not self._stop.is_set():
                try:
                    frame = np.asarray(self.camera.async_read(timeout_ms=500), dtype=np.uint8)
                    now_ns = time.monotonic_ns()
                    if frame.ndim != 3 or frame.shape[2] != 3:
                        raise ValueError(f"unexpected frame shape {frame.shape}")
                    with self._lock:
                        self._latest = frame.copy()
                    self.health.frame_count += 1
                    if not self.health.first_frame_ns:
                        self.health.first_frame_ns = now_ns
                    self.health.last_frame_ns = now_ns
                    self.health.height, self.health.width = frame.shape[:2]
                    if _is_green_frame(frame):
                        self.health.green_frames += 1
                    signature = float(frame[::32, ::32].mean())
                    if self.health.last_signature is not None and abs(signature - self.health.last_signature) < 0.01:
                        self.health.frozen_frames += 1
                    self.health.last_signature = signature
                    self.on_frame(self.name, frame, now_ns)
                except (RuntimeError, TimeoutError, ValueError) as exc:
                    self.health.read_errors += 1
                    self.health.error = str(exc)
        except Exception as exc:
            self.health.error = str(exc)
        finally:
            if self.health.connected:
                try:
                    self.camera.disconnect()
                except Exception:
                    pass
            self.health.connected = False

    def latest(self) -> np.ndarray | None:
        with self._lock:
            return None if self._latest is None else self._latest.copy()

    def status(self, now_ns: int | None = None) -> dict[str, object]:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        age_s = (now_ns - self.health.last_frame_ns) / 1e9 if self.health.last_frame_ns else None
        green_ratio = self.health.green_frames / max(self.health.frame_count, 1)
        frozen_ratio = self.health.frozen_frames / max(self.health.frame_count - 1, 1)
        elapsed_s = (self.health.last_frame_ns - self.health.first_frame_ns) / 1e9
        measured_fps = (self.health.frame_count - 1) / elapsed_s if elapsed_s > 0 else 0.0
        if self.health.error and not self.health.connected:
            level, message = "BLOCKED", self.health.error
        elif age_s is None or age_s > 1.5:
            level, message = "BLOCKED", "no fresh frame"
        elif self.health.frame_count < 30:
            level, message = "BLOCKED", f"warming up {self.health.frame_count}/30 frames"
        elif self.health.frame_count >= 10 and green_ratio > 0.8:
            level, message = "BLOCKED", f"green-frame ratio {green_ratio:.0%}"
        elif self.health.frame_count >= 30 and frozen_ratio > 0.9:
            level, message = "BLOCKED", f"frozen-frame ratio {frozen_ratio:.0%}"
        elif self.health.frame_count >= 30 and measured_fps < 20.0:
            level, message = "BLOCKED", f"capture rate {measured_fps:.1f} FPS is below 20 FPS"
        elif self.health.read_errors:
            level, message = "WARN", f"{self.health.read_errors} read errors, age {age_s:.2f}s"
        else:
            level, message = "PASS", f"{self.health.width}x{self.health.height}, {measured_fps:.1f} FPS"
        return {
            "level": level,
            "message": message,
            "frame_count": self.health.frame_count,
            "read_errors": self.health.read_errors,
            "green_ratio": green_ratio,
            "frozen_ratio": frozen_ratio,
            "measured_fps": measured_fps,
            "last_frame_ns": self.health.last_frame_ns,
        }

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)


@dataclass
class _PendingCameraFrame:
    camera_name: str
    frame: np.ndarray
    host_monotonic_ns: int
    source_timestamp_ns: int | None
    sensor_timestamp_ns: int | None


class SynchronizedCameraRecorder:
    """Group independently arriving camera frames before they enter the writer."""

    def __init__(
        self,
        camera_names: tuple[str, ...] | list[str],
        on_frame: Callable[..., bool | None],
        *,
        max_skew_ms: float = 40.0,
        max_buffered_frames_per_camera: int = 8,
    ):
        if not camera_names:
            raise ValueError("at least one camera is required")
        self.camera_names = tuple(camera_names)
        if len(set(self.camera_names)) != len(self.camera_names):
            raise ValueError("camera names must be unique")
        self._on_frame = on_frame
        self._max_skew_ns = int(max_skew_ms * 1_000_000)
        self._max_skew_ms = float(max_skew_ms)
        self._max_buffered = max(1, int(max_buffered_frames_per_camera))
        self._active = False
        self._buffers: dict[str, deque[_PendingCameraFrame]] = {
            name: deque() for name in self.camera_names
        }
        self._lock = threading.Lock()
        self._next_group_id = 0
        self._dropped_frames = 0
        self._emitted_groups = 0

    @property
    def dropped_frames(self) -> int:
        with self._lock:
            return self._dropped_frames

    @property
    def emitted_groups(self) -> int:
        with self._lock:
            return self._emitted_groups

    def reset(self) -> None:
        with self._lock:
            self._reset_locked()

    def set_active(self, active: bool) -> None:
        with self._lock:
            active = bool(active)
            if self._active != active:
                self._reset_locked()
            self._active = active

    def start_episode(self) -> None:
        self.set_active(True)

    def stop_episode(self) -> None:
        self.set_active(False)

    def _reset_locked(self) -> None:
        for frames in self._buffers.values():
            frames.clear()
        self._next_group_id = 0
        self._dropped_frames = 0
        self._emitted_groups = 0

    def __call__(
        self,
        camera_name: str,
        frame: np.ndarray,
        host_monotonic_ns: int,
        *,
        source_timestamp_ns: int | None = None,
        sensor_timestamp_ns: int | None = None,
    ) -> bool:
        with self._lock:
            if not self._active:
                return False
            if camera_name not in self._buffers:
                return False
            pending = _PendingCameraFrame(
                camera_name=str(camera_name),
                frame=np.asarray(frame, dtype=np.uint8).copy(),
                host_monotonic_ns=int(host_monotonic_ns),
                source_timestamp_ns=source_timestamp_ns,
                sensor_timestamp_ns=sensor_timestamp_ns,
            )
            camera_buffer = self._buffers[camera_name]
            camera_buffer.append(pending)
            while len(camera_buffer) > self._max_buffered:
                camera_buffer.popleft()
                self._dropped_frames += 1
            return self._emit_ready_groups_locked()

    def _emit_ready_groups_locked(self) -> bool:
        emitted = False
        while all(self._buffers[name] for name in self.camera_names):
            heads = {name: self._buffers[name][0] for name in self.camera_names}
            oldest_name, oldest = min(heads.items(), key=lambda item: item[1].host_monotonic_ns)
            newest = max(frame.host_monotonic_ns for frame in heads.values())
            skew_ns = newest - oldest.host_monotonic_ns
            if skew_ns > self._max_skew_ns:
                self._buffers[oldest_name].popleft()
                self._dropped_frames += 1
                continue

            group_id = self._next_group_id
            self._next_group_id += 1
            skew_ms = round(skew_ns / 1e6, 6)
            dropped_before_group = self._dropped_frames
            group = [self._buffers[name].popleft() for name in self.camera_names]
            for item in group:
                result = self._on_frame(
                    item.camera_name,
                    item.frame,
                    item.host_monotonic_ns,
                    source_timestamp_ns=item.source_timestamp_ns,
                    sensor_timestamp_ns=item.sensor_timestamp_ns,
                    camera_stream_sequence_id=group_id,
                    camera_sync_group_skew_ms=skew_ms,
                    camera_sync_group_size=len(self.camera_names),
                    camera_alignment_dropped_frames=dropped_before_group,
                    camera_sync_threshold_ms=self._max_skew_ms,
                )
                if result is False:
                    self._dropped_frames += 1
            self._emitted_groups += 1
            emitted = True
        return emitted

    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "emitted_groups": self._emitted_groups,
                "dropped_frames": self._dropped_frames,
                "active": self._active,
                "buffered_frames": {name: len(frames) for name, frames in self._buffers.items()},
                "max_skew_ms": self._max_skew_ms,
            }


class CameraManager:
    def __init__(self, cameras: dict[str, Any], on_frame: Callable[[str, np.ndarray, int], None]):
        self.workers = {
            name: CameraCaptureThread(name, camera, on_frame) for name, camera in cameras.items()
        }

    def start(self) -> None:
        for worker in self.workers.values():
            worker.start()

    def latest(self, name: str) -> np.ndarray | None:
        worker = self.workers.get(name)
        return None if worker is None else worker.latest()

    def status(self) -> dict[str, dict[str, object]]:
        now_ns = time.monotonic_ns()
        return {name: worker.status(now_ns) for name, worker in self.workers.items()}

    def close(self) -> None:
        for worker in self.workers.values():
            worker.close()


class CameraMode(str, Enum):
    OFF = "off"
    MOSAIC = "mosaic"


class SwitchableCameraManager:
    """Own camera device lifetimes and permit IDLE-only mode switches."""

    def __init__(
        self,
        camera_factory: Callable[[], dict[str, Any]],
        on_frame: Callable[[str, np.ndarray, int], None],
    ):
        self._camera_factory = camera_factory
        self._on_frame = on_frame
        self._manager: CameraManager | None = None
        self._mode = CameraMode.OFF
        self._lock = threading.RLock()

    @property
    def mode(self) -> CameraMode:
        with self._lock:
            return self._mode

    @property
    def workers(self) -> dict[str, CameraCaptureThread]:
        with self._lock:
            return {} if self._manager is None else dict(self._manager.workers)

    def set_mode(self, mode: str | CameraMode) -> CameraMode:
        requested = CameraMode(mode)
        with self._lock:
            if requested == self._mode:
                return self._mode
            if self._manager is not None:
                self._manager.close()
                self._manager = None
            if requested == CameraMode.MOSAIC:
                manager = CameraManager(self._camera_factory(), self._on_frame)
                manager.start()
                self._manager = manager
            self._mode = requested
            return self._mode

    def latest(self, name: str) -> np.ndarray | None:
        with self._lock:
            return None if self._manager is None else self._manager.latest(name)

    def status(self) -> dict[str, dict[str, object]]:
        with self._lock:
            return {} if self._manager is None else self._manager.status()

    def close(self) -> None:
        with self._lock:
            if self._manager is not None:
                self._manager.close()
                self._manager = None
            self._mode = CameraMode.OFF
