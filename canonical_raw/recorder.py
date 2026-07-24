"""Crash-tolerant asynchronous writer for Canonical Raw episodes."""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

from .contract import (
    ACTION_SEMANTICS_VERSION,
    CAPTURE_CONTRACT_VERSION,
    default_action_semantics,
    default_timebase_contract,
    session_event,
)


STREAM_FILES = {
    "control": "control",
    "robot_feedback": "robot_feedback",
    "vr_input": "vr_input",
    "camera": "camera_timestamps",
    "event": "events",
    "language_action": "language_actions",
}


class RecorderState(str, Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    FINALIZING = "FINALIZING"
    ERROR = "ERROR"
    CLOSED = "CLOSED"


@dataclass
class RecorderStatus:
    state: RecorderState = RecorderState.IDLE
    episode_id: str | None = None
    episode_path: str | None = None
    error: str | None = None
    dropped_camera_frames: int = 0


def _json_default(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _write_json(path: Path, value: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def _append_jsonl(stream: Any, value: object) -> None:
    stream.write(json.dumps(value, separators=(",", ":"), default=_json_default) + "\n")
    stream.flush()
    os.fsync(stream.fileno())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class _EpisodeWriter:
    def __init__(
        self,
        session_dir: Path,
        metadata: dict[str, Any],
        camera_fps: float,
        *,
        enable_mcap: bool,
        mcap_jpeg_quality: int,
        calibration_snapshot: dict[str, Any] | None,
    ):
        self.metadata = dict(metadata)
        self.episode_id = str(self.metadata["episode_id"])
        self.final_dir = session_dir / self.episode_id
        self.work_dir = session_dir / f"{self.episode_id}.inprogress"
        if self.final_dir.exists() or self.work_dir.exists():
            raise FileExistsError(f"episode already exists: {self.episode_id}")
        self.work_dir.mkdir(parents=True)
        self.metadata["recording_state"] = "inprogress"
        _write_json(self.work_dir / "metadata.json", self.metadata)

        self._streams = {
            kind: (self.work_dir / f"{stem}.jsonl.inprogress").open("a", encoding="utf-8", buffering=1)
            for kind, stem in STREAM_FILES.items()
        }
        self._video_writers: dict[str, Any] = {}
        self._video_shape: dict[str, tuple[int, int]] = {}
        self._camera_fps = camera_fps
        self._counts = {kind: 0 for kind in STREAM_FILES}
        self._camera_counts: dict[str, int] = {}
        self._stream_sequences = {kind: 0 for kind in STREAM_FILES}
        self._camera_first_ns: dict[str, int] = {}
        self._camera_last_ns: dict[str, int] = {}
        self._mcap_requested = bool(enable_mcap)
        self._mcap = None
        self._mcap_error: str | None = None
        if enable_mcap:
            try:
                from mcap_log.writer import McapLogWriter

                self._mcap = McapLogWriter(
                    self.work_dir,
                    episode_metadata=self.metadata,
                    calibration_snapshot=calibration_snapshot,
                    jpeg_quality=mcap_jpeg_quality,
                )
            except Exception as exc:
                self._mcap_error = repr(exc)

    def _try_mcap(self, operation: Any, *args: Any, **kwargs: Any) -> None:
        if self._mcap is None or self._mcap_error is not None:
            return
        try:
            operation(*args, **kwargs)
        except Exception as exc:
            self._mcap_error = repr(exc)
            try:
                self._mcap.abandon()
            except Exception:
                pass

    def _enrich_row(self, kind: str, row: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(row)
        sequence = self._stream_sequences[kind]
        self._stream_sequences[kind] = sequence + 1
        enriched.setdefault("row_sequence_id", sequence)
        host_ns = enriched.get("host_monotonic_ns")
        if host_ns is not None:
            enriched["host_monotonic_ns"] = int(host_ns)
            enriched.setdefault("source_timestamp_ns", int(host_ns))
        enriched.setdefault("host_wall_time_ns", time.time_ns())
        if kind in {"control", "robot_feedback", "vr_input"}:
            enriched.setdefault("control_sample_index", sequence)
            enriched.setdefault("sample_id", f"{self.episode_id}:{int(enriched['control_sample_index']):06d}")
            enriched.setdefault("frame_id", {
                "control": "robot_base",
                "robot_feedback": "robot_base",
                "vr_input": "quest_world",
            }[kind])
        if kind == "control":
            for field in (
                "action_request_generated_host_monotonic_ns", "action_send_start_host_monotonic_ns",
                "action_send_end_host_monotonic_ns", "action_send_result_received_host_monotonic_ns",
            ):
                enriched.setdefault(field, int(host_ns or time.monotonic_ns()))
        elif kind == "robot_feedback":
            enriched.setdefault("robot_feedback_source_timestamp_ns", None)
            enriched.setdefault("robot_feedback_source_timestamp_unavailable_reason", "hardware_timestamp_unavailable")
            for field in ("robot_feedback_read_start_host_monotonic_ns", "robot_feedback_host_receive_monotonic_ns", "robot_feedback_enqueue_host_monotonic_ns"):
                enriched.setdefault(field, int(host_ns or time.monotonic_ns()))
        elif kind == "vr_input":
            enriched.setdefault("controller_event_source_timestamp_ns", None)
            enriched.setdefault("controller_event_source_timestamp_unavailable_reason", "quest_device_timestamp_unavailable")
            enriched.setdefault("controller_event_host_receive_monotonic_ns", None)
            enriched.setdefault("controller_event_host_receive_unavailable_reason", "no_controller_event")
            enriched.setdefault("controller_event_enqueue_host_monotonic_ns", time.monotonic_ns())
            enriched.setdefault("controller_event_age_s", None)
            enriched.setdefault("controller_event_count", None)
        return enriched

    def write_row(self, kind: str, row: dict[str, Any]) -> None:
        row = self._enrich_row(kind, row)
        self._streams[kind].write(json.dumps(row, separators=(",", ":"), default=_json_default) + "\n")
        self._counts[kind] += 1
        if self._mcap is not None:
            self._try_mcap(self._mcap.write_row, kind, row)

    def write_mcap(self, topic: str, row: dict[str, Any]) -> None:
        if self._mcap is not None:
            self._try_mcap(self._mcap.write_json, topic, row)

    def write_camera(self, message: dict[str, Any]) -> None:
        import cv2

        name = str(message["camera_name"])
        message["camera_write_host_monotonic_ns"] = time.monotonic_ns()
        message.setdefault("camera_stream_sequence_id", self._camera_counts.get(name, 0))
        frame = np.asarray(message.pop("frame"), dtype=np.uint8)
        if self._mcap is not None:
            self._try_mcap(
                self._mcap.write_camera,
                name,
                frame,
                lifecycle=message,
            )
        if frame.ndim != 3 or frame.shape[2] != 3:
            message["decoded"] = False
            self.write_row("camera", message)
            return

        height, width = frame.shape[:2]
        writer = self._video_writers.get(name)
        if writer is None:
            path = self.work_dir / f"camera_{name}.inprogress.mp4"
            writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                self._camera_fps,
                (width, height),
            )
            if not writer.isOpened():
                raise RuntimeError(f"failed to open video writer for {name}")
            self._video_writers[name] = writer
            self._video_shape[name] = (width, height)
        if self._video_shape[name] != (width, height):
            frame = cv2.resize(frame, self._video_shape[name])

        writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        message["video_frame_index"] = self._camera_counts.get(name, 0)
        message["camera_stream_sequence_id"] = message["video_frame_index"]
        message["width"] = width
        message["height"] = height
        message["decoded"] = True
        source_ns = int(message.get("source_timestamp_ns") or message["host_monotonic_ns"])
        self._camera_first_ns.setdefault(name, source_ns)
        self._camera_last_ns[name] = source_ns
        self.write_row("camera", message)
        self._camera_counts[name] = self._camera_counts.get(name, 0) + 1

    def _camera_sync_metrics(self) -> dict[str, Any]:
        counts = dict(sorted(self._camera_counts.items()))
        if not counts:
            return {
                "camera_frame_counts": {},
                "max_frame_count_skew": 0,
                "first_frame_skew_ms": 0.0,
                "last_frame_skew_ms": 0.0,
            }
        frame_skew = max(counts.values()) - min(counts.values())
        first_values = list(self._camera_first_ns.values())
        last_values = list(self._camera_last_ns.values())
        return {
            "camera_frame_counts": counts,
            "max_frame_count_skew": frame_skew,
            "first_frame_skew_ms": round((max(first_values) - min(first_values)) / 1e6, 6)
            if first_values
            else 0.0,
            "last_frame_skew_ms": round((max(last_values) - min(last_values)) / 1e6, 6)
            if last_values
            else 0.0,
        }

    def _close_streams(self) -> None:
        for stream in self._streams.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        for writer in self._video_writers.values():
            writer.release()

    def _jsonl_to_parquet(self, kind: str) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        stem = STREAM_FILES[kind]
        source = self.work_dir / f"{stem}.jsonl.inprogress"
        destination = self.work_dir / f"{stem}.parquet"
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
        if rows:
            pq.write_table(pa.Table.from_pylist(rows), destination, compression="zstd")
        source.rename(self.work_dir / f"{stem}.jsonl")

    def finalize(self, result: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        self._close_streams()
        for kind in ("control", "robot_feedback", "vr_input", "camera", "language_action"):
            self._jsonl_to_parquet(kind)
        event_source = self.work_dir / "events.jsonl.inprogress"
        event_source.rename(self.work_dir / "events.jsonl")
        for name in self._video_writers:
            source = self.work_dir / f"camera_{name}.inprogress.mp4"
            source.rename(self.work_dir / f"camera_{name}.mp4")

        if self._mcap is not None and self._mcap_error is None:
            try:
                self._mcap.finish(result)
            except Exception as exc:
                self._mcap_error = repr(exc)
                try:
                    self._mcap.abandon()
                except Exception:
                    pass

        end_ns = time.monotonic_ns()
        start_ns = int(
            self.metadata.get("episode_start_host_monotonic_ns")
            or self.metadata.get("start_host_monotonic_ns")
            or end_ns
        )
        self.metadata.update(result)
        self.metadata.update(
            {
                "end_host_monotonic_ns": end_ns,
                "episode_end_host_monotonic_ns": end_ns,
                "duration_s": max(0.0, (end_ns - start_ns) / 1e9),
                "recording_state": "finalized",
                "stream_counts": self._counts,
                "stream_sequence_counts": self._stream_sequences,
                "camera_sync": self._camera_sync_metrics(),
                "mcap_log": {
                    "enabled": self._mcap_requested,
                    "status": (
                        "disabled"
                        if not self._mcap_requested
                        else "complete"
                        if self._mcap is not None and self._mcap_error is None
                        else "error"
                    ),
                    "error": self._mcap_error,
                },
            }
        )
        _write_json(self.work_dir / "metadata.json", self.metadata)

        files = []
        for item in sorted(self.work_dir.iterdir()):
            if item.is_file() and item.name != "manifest.json":
                files.append({"path": item.name, "bytes": item.stat().st_size, "sha256": _sha256(item)})
        manifest = {
            "schema_version": "piper_canonical_raw_v1",
            "episode_id": self.episode_id,
            "created_host_monotonic_ns": end_ns,
            "files": files,
            "stream_counts": self._counts,
            "stream_sequence_counts": self._stream_sequences,
            "camera_sync": self._camera_sync_metrics(),
        }
        _write_json(self.work_dir / "manifest.json", manifest)
        os.replace(self.work_dir, self.final_dir)
        return self.final_dir, manifest

    def abort(self, reason: str, *, recording_state: str = "aborted") -> Path:
        if recording_state not in {"aborted", "incomplete"}:
            raise ValueError(f"invalid interrupted recording state: {recording_state}")
        self._close_streams()
        result = {"task_success": False, "failure_reason": reason}
        if self._mcap is not None and self._mcap_error is None:
            try:
                self._mcap.finish(result, aborted=True)
            except Exception as exc:
                self._mcap_error = repr(exc)
                try:
                    self._mcap.abandon()
                except Exception:
                    pass
        end_ns = time.monotonic_ns()
        start_ns = int(
            self.metadata.get("episode_start_host_monotonic_ns")
            or self.metadata.get("start_host_monotonic_ns")
            or end_ns
        )
        self.metadata.update(
            {
                "end_host_monotonic_ns": end_ns,
                "episode_end_host_monotonic_ns": end_ns,
                "duration_s": max(0.0, (end_ns - start_ns) / 1e9),
                "termination_reason": reason,
                "recording_state": recording_state,
                "task_success": False,
                "failure_reason": reason,
                "stream_counts": self._counts,
                "stream_sequence_counts": self._stream_sequences,
                "camera_sync": self._camera_sync_metrics(),
                "mcap_log": {
                    "enabled": self._mcap_requested,
                    "status": (
                        "disabled"
                        if not self._mcap_requested
                        else "aborted"
                        if self._mcap is not None and self._mcap_error is None
                        else "error"
                    ),
                    "error": self._mcap_error,
                },
            }
        )
        _write_json(self.work_dir / "metadata.json", self.metadata)
        return self.work_dir


def _worker_main(
    output_root: str,
    session_id: str,
    session_metadata: dict[str, Any],
    camera_fps: float,
    command_queue: mp.Queue,
    camera_queue: mp.Queue,
    status_queue: mp.Queue,
    enable_mcap: bool,
    mcap_jpeg_quality: int,
    calibration_snapshot: dict[str, Any] | None,
) -> None:
    episode: _EpisodeWriter | None = None
    camera_barriers: set[str] = set()

    def consume_camera(message: dict[str, Any]) -> None:
        if message.get("kind") == "barrier":
            camera_barriers.add(str(message["token"]))
        elif episode is not None:
            episode.write_camera(message)

    def drain_through_barrier(token: str) -> None:
        deadline = time.monotonic() + 10.0
        while token not in camera_barriers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("camera queue barrier timed out")
            consume_camera(camera_queue.get(timeout=remaining))
    try:
        session_dir = Path(output_root) / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        _write_json(session_dir / "session.json", session_metadata)
        session_events = (session_dir / "session_events.jsonl").open("a", encoding="utf-8", buffering=1)
        session_event_sequence = 0
        last_session_event_ns = 0

        def append_session_event(envelope: dict[str, Any]) -> None:
            nonlocal session_event_sequence, last_session_event_ns
            now_ns = max(time.monotonic_ns(), last_session_event_ns + 1)
            last_session_event_ns = now_ns
            row = {
                "session_event_sequence_id": session_event_sequence,
                "host_monotonic_ns": now_ns,
                "host_wall_time_ns": time.time_ns(),
                **envelope,
            }
            # Keep the legacy key readable while consumers migrate to event_type.
            row["event"] = row["event_type"]
            session_event_sequence += 1
            _append_jsonl(session_events, row)

        append_session_event(session_event("session_start", source="recorder", payload={"session_id": session_id}))
        status_queue.put({"type": "ready", "session_dir": str(session_dir)})

        running = True
        while running:
            try:
                command = command_queue.get(timeout=0.01)
            except queue.Empty:
                command = None

            if command is not None:
                kind = command["kind"]
                if kind == "start":
                    if episode is not None:
                        raise RuntimeError("an episode is already recording")
                    episode = _EpisodeWriter(
                        session_dir,
                        command["metadata"],
                        camera_fps,
                        enable_mcap=enable_mcap,
                        mcap_jpeg_quality=mcap_jpeg_quality,
                        calibration_snapshot=calibration_snapshot,
                    )
                    episode.write_row("event", command["event"])
                    append_session_event(session_event(
                        "operator_start",
                        episode_id=episode.episode_id,
                        source=str(command.get("source") or "operator"),
                        payload={
                            "task_id": episode.metadata.get("task_id"),
                            "operator_id": episode.metadata.get("operator_id"),
                        },
                    ))
                    status_queue.put({"type": "started", "episode_id": episode.episode_id})
                elif kind == "session_event":
                    append_session_event(dict(command["envelope"]))
                elif kind in {"control", "robot_feedback", "vr_input", "event", "language_action"} and episode is not None:
                    episode.write_row(kind, command["row"])
                elif kind == "mcap" and episode is not None:
                    episode.write_mcap(str(command["topic"]), command["row"])
                elif kind in {"finish", "abort", "interrupt"} and episode is not None:
                    drain_through_barrier(str(command["camera_barrier"]))
                    if kind == "finish":
                        path, manifest = episode.finalize(command["result"])
                        succeeded = bool(command["result"].get("task_success"))
                        append_session_event(session_event(
                            "success" if succeeded else "failure",
                            episode_id=episode.episode_id,
                            reason=str(command["result"].get("termination_reason") or "operator_stop"),
                            source=str(command.get("source") or "operator"),
                            payload={"task_success": succeeded},
                        ))
                        append_session_event(session_event(
                            "operator_stop", episode_id=episode.episode_id,
                            reason=str(command["result"].get("termination_reason") or "operator_stop"),
                            source=str(command.get("source") or "operator"),
                        ))
                        status_queue.put({"type": "finished", "path": str(path), "manifest": manifest})
                    elif kind == "abort":
                        path = episode.abort(command["reason"])
                        append_session_event(session_event(
                            str(command.get("event_type") or "operator_abort"), episode_id=episode.episode_id,
                            reason=command["reason"], source=str(command.get("source") or "operator"),
                        ))
                        status_queue.put({"type": "aborted", "path": str(path)})
                    else:
                        path = episode.abort(command["reason"], recording_state="incomplete")
                        append_session_event(session_event(
                            "process_interruption", episode_id=episode.episode_id,
                            reason=command["reason"], source=str(command.get("source") or "process"),
                        ))
                        status_queue.put({"type": "interrupted", "path": str(path)})
                    episode = None
                elif kind == "shutdown":
                    if episode is not None:
                        path = episode.abort("process_shutdown", recording_state="incomplete")
                        append_session_event(session_event(
                            "process_interruption", episode_id=episode.episode_id,
                            reason="process_shutdown", source="process",
                        ))
                        status_queue.put({"type": "aborted", "path": str(path)})
                        episode = None
                    append_session_event(session_event(
                        "process_shutdown", reason="orderly_shutdown", source="process",
                        payload={"session_id": session_id},
                    ))
                    running = False

            if episode is not None:
                for _ in range(3):
                    try:
                        camera = camera_queue.get_nowait()
                    except queue.Empty:
                        break
                    consume_camera(camera)
        session_events.close()
    except Exception as exc:
        status_queue.put({"type": "error", "error": repr(exc)})
        if episode is not None:
            try:
                episode.abort(f"writer_error:{type(exc).__name__}", recording_state="incomplete")
            except Exception:
                pass


class AsyncCanonicalRecorder:
    """Non-blocking facade over the dedicated Canonical Raw writer process."""

    def __init__(
        self,
        *,
        output_root: Path,
        session_metadata: dict[str, Any],
        camera_fps: float = 30.0,
        camera_queue_size: int = 180,
        enable_mcap: bool = False,
        mcap_jpeg_quality: int = 90,
        calibration_snapshot: dict[str, Any] | None = None,
    ):
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.session_id = f"session_{timestamp}_{uuid.uuid4().hex[:6]}"
        self.output_root = Path(output_root)
        self.recoverable_episodes = sorted(str(path) for path in self.output_root.glob("session_*/*.inprogress"))
        self._command_queue: mp.Queue = mp.Queue(maxsize=2048)
        self._camera_queue: mp.Queue = mp.Queue(maxsize=camera_queue_size)
        self._status_queue: mp.Queue = mp.Queue()
        self._status_lock = threading.Lock()
        self._status = RecorderStatus()
        self.enable_mcap = bool(enable_mcap)
        metadata = dict(session_metadata)
        metadata.update({"session_id": self.session_id, "schema_version": "piper_canonical_raw_v1"})
        self._process = mp.Process(
            target=_worker_main,
            args=(
                str(self.output_root),
                self.session_id,
                metadata,
                camera_fps,
                self._command_queue,
                self._camera_queue,
                self._status_queue,
                self.enable_mcap,
                int(mcap_jpeg_quality),
                calibration_snapshot,
            ),
            name="canonical-raw-writer",
        )
        self._process.start()
        self._wait_for("ready", timeout=10.0)

    @property
    def state(self) -> RecorderState:
        self._drain_status()
        return self._status.state

    @property
    def is_recording(self) -> bool:
        return self.state == RecorderState.RECORDING

    @property
    def status(self) -> dict[str, Any]:
        self._drain_status()
        return {
            "state": self._status.state.value,
            "episode_id": self._status.episode_id,
            "episode_path": self._status.episode_path,
            "error": self._status.error,
            "dropped_camera_frames": self._status.dropped_camera_frames,
            "writer_alive": self._process.is_alive(),
            "session_id": self.session_id,
            "recoverable_episodes": self.recoverable_episodes,
            "mcap_enabled": self.enable_mcap,
        }

    def _apply_status(self, message: dict[str, Any]) -> None:
        kind = message["type"]
        if kind == "started":
            self._status.state = RecorderState.RECORDING
            self._status.episode_id = message["episode_id"]
        elif kind in {"finished", "aborted", "interrupted"}:
            self._status.state = RecorderState.IDLE
            self._status.episode_path = message["path"]
            self._status.episode_id = None
        elif kind == "error":
            self._status.state = RecorderState.ERROR
            self._status.error = message["error"]
        elif kind == "ready":
            self._status.state = RecorderState.IDLE

    def _drain_status(self) -> None:
        if not self._status_lock.acquire(blocking=False):
            return
        try:
            while True:
                try:
                    self._apply_status(self._status_queue.get_nowait())
                except queue.Empty:
                    return
        finally:
            self._status_lock.release()

    def _wait_for(self, expected: str, timeout: float) -> dict[str, Any]:
        with self._status_lock:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remaining = max(0.01, deadline - time.monotonic())
                try:
                    message = self._status_queue.get(timeout=remaining)
                except queue.Empty:
                    break
                self._apply_status(message)
                if message["type"] == "error":
                    raise RuntimeError(message["error"])
                if message["type"] == expected:
                    return message
        raise TimeoutError(f"writer did not report {expected!r} within {timeout:.1f}s")

    def start_episode(self, metadata: dict[str, Any]) -> str:
        if self.state != RecorderState.IDLE:
            raise RuntimeError(f"cannot start from recorder state {self.state.value}")
        episode_id = str(metadata.get("episode_id") or f"episode_{time.strftime('%Y%m%d_%H%M%S')}")
        now = time.monotonic_ns()
        episode_metadata = dict(metadata)
        self._status.dropped_camera_frames = 0
        episode_metadata.update(
            {
                "episode_id": episode_id,
                "schema_version": "piper_canonical_raw_v1",
                "capture_contract_version": CAPTURE_CONTRACT_VERSION,
                "timebase": default_timebase_contract(),
                "action_semantics_version": ACTION_SEMANTICS_VERSION,
                "action_semantics": {
                    **default_action_semantics(),
                    **dict(episode_metadata.get("action_semantics") or {}),
                },
                "start_host_monotonic_ns": now,
                "episode_start_host_monotonic_ns": now,
                "slicing_rule": "session_event_episode_boundaries_v1",
                "session_id": self.session_id,
            }
        )
        self._command_queue.put(
            {
                "kind": "start",
                "metadata": episode_metadata,
                "event": {"host_monotonic_ns": now, "event": "recording_started"},
            }
        )
        self._wait_for("started", timeout=10.0)
        return episode_id

    def record_row(self, kind: str, row: dict[str, Any]) -> None:
        if self._status.state != RecorderState.RECORDING:
            return
        if kind not in {"control", "robot_feedback", "vr_input", "event", "language_action"}:
            raise ValueError(f"unsupported stream kind: {kind}")
        self._command_queue.put({"kind": kind, "row": row}, timeout=1.0)

    def record_mcap(self, topic: str, row: dict[str, Any]) -> None:
        if not self.enable_mcap or self._status.state != RecorderState.RECORDING:
            return
        self._command_queue.put({"kind": "mcap", "topic": topic, "row": row}, timeout=1.0)

    def record_session_event(
        self,
        event_type: str,
        *,
        episode_id: str | None = None,
        reason: str | None = None,
        source: str = "system",
        payload: dict[str, Any] | None = None,
        **legacy_payload: Any,
    ) -> None:
        if self._status.state != RecorderState.CLOSED:
            merged_payload = {**dict(payload or {}), **legacy_payload}
            self._command_queue.put(
                {
                    "kind": "session_event",
                    "envelope": session_event(
                        event_type,
                        episode_id=episode_id,
                        reason=reason,
                        source=source,
                        payload=merged_payload,
                    ),
                },
                timeout=1.0,
            )

    def record_camera(
        self,
        camera_name: str,
        frame: np.ndarray,
        host_monotonic_ns: int,
        *,
        source_timestamp_ns: int | None = None,
        sensor_timestamp_ns: int | None = None,
    ) -> bool:
        if self._status.state != RecorderState.RECORDING:
            return False
        message = {
            "kind": "camera",
            "camera_name": camera_name,
            "host_monotonic_ns": int(host_monotonic_ns),
            "camera_enqueue_host_monotonic_ns": time.monotonic_ns(),
            "host_wall_time_ns": time.time_ns(),
            "source_timestamp_ns": int(source_timestamp_ns or host_monotonic_ns),
            "camera_sensor_timestamp_ns": sensor_timestamp_ns,
            "camera_sensor_timestamp_unavailable_reason": None if sensor_timestamp_ns is not None else "camera_sdk_timestamp_unavailable",
            "camera_host_receive_monotonic_ns": int(host_monotonic_ns),
            "frame": np.asarray(frame, dtype=np.uint8),
        }
        try:
            self._camera_queue.put_nowait(message)
            return True
        except queue.Full:
            self._status.dropped_camera_frames += 1
            return False

    def finish_episode(
        self,
        *,
        task_success: bool,
        failure_reason: str,
        outcomes: dict[str, bool] | None = None,
        metadata_updates: dict[str, Any] | None = None,
        source: str = "operator",
    ) -> Path:
        if not self.is_recording:
            raise RuntimeError("no episode is recording")
        self._status.state = RecorderState.FINALIZING
        barrier = uuid.uuid4().hex
        self._camera_queue.put({"kind": "barrier", "token": barrier}, timeout=10.0)
        self._command_queue.put(
            {
                "kind": "finish",
                "source": source,
                "camera_barrier": barrier,
                "result": {
                    "task_success": bool(task_success),
                    "failure_reason": "none" if task_success else failure_reason,
                    "termination_reason": "operator_success" if task_success else failure_reason,
                    "outcomes": outcomes or {},
                    "dropped_camera_frames": self._status.dropped_camera_frames,
                    **(metadata_updates or {}),
                },
            }
        )
        message = self._wait_for("finished", timeout=90.0)
        return Path(message["path"])

    def abort_episode(
        self, reason: str = "operator_abort", *, source: str = "operator",
        event_type: str = "operator_abort",
    ) -> Path:
        if not self.is_recording:
            raise RuntimeError("no episode is recording")
        self._status.state = RecorderState.FINALIZING
        barrier = uuid.uuid4().hex
        self._camera_queue.put({"kind": "barrier", "token": barrier}, timeout=10.0)
        self._command_queue.put(
            {"kind": "abort", "reason": reason, "camera_barrier": barrier, "source": source, "event_type": event_type}
        )
        return Path(self._wait_for("aborted", timeout=30.0)["path"])

    def interrupt_episode(self, reason: str = "process_interruption") -> Path:
        if not self.is_recording:
            raise RuntimeError("no episode is recording")
        self._status.state = RecorderState.FINALIZING
        barrier = uuid.uuid4().hex
        self._camera_queue.put({"kind": "barrier", "token": barrier}, timeout=10.0)
        self._command_queue.put({"kind": "interrupt", "reason": reason, "camera_barrier": barrier, "source": "process"})
        return Path(self._wait_for("interrupted", timeout=30.0)["path"])

    def close(self) -> None:
        if self._status.state == RecorderState.CLOSED:
            return
        self._command_queue.put({"kind": "shutdown"})
        self._process.join(timeout=15.0)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5.0)
        self._status.state = RecorderState.CLOSED
        for item in (self._command_queue, self._camera_queue, self._status_queue):
            item.close()
