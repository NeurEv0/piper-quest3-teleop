#!/usr/bin/env python3
"""User-facing dual-Piper Quest3 teleoperation with Canonical Raw recording."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cv2
import numpy as np

from canonical_raw.action_state import typed_control_fields, typed_feedback_fields
from canonical_raw.cameras import CameraMode, SwitchableCameraManager, SynchronizedCameraRecorder
from canonical_raw.contract import CAPTURE_CONTRACT_VERSION, collection_profile, default_action_space_contract
from canonical_raw.dashboard import OperatorDashboard
from canonical_raw.preflight import CheckResult, PreflightReport, run_static_preflight, validate_cleaning_ready
from canonical_raw.recorder import AsyncCanonicalRecorder, RecorderState
from canonical_raw.validator import validate_episode
from canonical_raw.vla_annotations import ANNOTATION_SCHEMA, ARMS, PRIMITIVES, normalize_english_text, normalize_language_action
from mcap_log.system_metrics import SystemMetricsSampler
from mcap_log.validator import validate_mcap
from lerobot.cameras.utils import make_cameras_from_configs
from lerobot_robot_bi_piper_quest3.bi_piper_quest3 import BiPiperQuest3
from lerobot_robot_bi_piper_quest3.config_bi_piper_quest3 import BiPiperQuest3Config, _default_cameras
from lerobot_teleoperator_bi_quest3_vr.bi_quest3_vr import BiQuest3VR
from lerobot_teleoperator_bi_quest3_vr.config_bi_quest3_vr import BiQuest3VRConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left-can", default="can_left")
    parser.add_argument("--right-can", default="can_right")
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--camera-fps", type=float, default=30.0)
    parser.add_argument("--output-root", type=Path, default=Path("/home/ylhp-e-ai/ZHITAI_1t/TELEOP/piper_canonical_raw"))
    parser.add_argument("--dashboard-host", default="0.0.0.0")
    parser.add_argument("--dashboard-port", type=int, default=8020)
    parser.add_argument("--robot-connect-timeout", type=float, default=15.0)
    parser.add_argument("--minimum-free-gib", type=float, default=20.0)
    parser.add_argument("--mock-hardware", action="store_true")
    parser.add_argument("--mock-vr", action="store_true")
    parser.add_argument("--allow-no-cameras", action="store_true", help="Testing only; camera-less episodes are invalid for training")
    parser.add_argument("--camera-mode", choices=[mode.value for mode in CameraMode], default=CameraMode.MOSAIC.value)
    parser.add_argument("--allow-no-estop", action="store_true", help="Unsafe override for bench diagnostics only")
    parser.add_argument("--auto-start", action="store_true", help="Start a test episode immediately")
    parser.add_argument("--duration", type=float, default=0.0, help="Auto-finish after this many seconds")
    parser.add_argument("--operator-id", default="operator")
    parser.add_argument("--task-id", default="bimanual_manipulation")
    parser.add_argument("--language-instruction", default="Perform the demonstrated bimanual manipulation.")
    parser.add_argument("--robot-id", default="dual_piper_rig_01")
    parser.add_argument("--scene-id", default="scene_default")
    parser.add_argument("--enable-mcap", action="store_true", help="Also write the versioned raw MCAP sidecar")
    parser.add_argument("--mcap-jpeg-quality", type=int, default=90)
    parser.add_argument("--calibration-file", type=Path, default=REPO_ROOT / "calibration" / "rig_current.json")
    return parser.parse_args()


def _load_calibration(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("top-level value must be an object")
        value["source_file"] = str(path)
        return value
    except Exception as exc:
        return {
            "calibration_version": "missing",
            "status": "missing",
            "source_file": str(path),
            "error": repr(exc),
            "transforms": [],
        }


def _connect_robot_with_timeout(robot: BiPiperQuest3, timeout_s: float) -> None:
    """Interrupt SDK enable loops that otherwise wait forever without CAN feedback."""
    if timeout_s <= 0:
        robot.connect()
        return

    def timeout_handler(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"robot did not respond on CAN within {timeout_s:.1f}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        robot.connect()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _wait_for_vuer_server(teleop: BiQuest3VR, port: int = 8012, timeout_s: float = 5.0) -> None:
    """Require a live Vuer child and listening socket before enabling hardware."""
    if teleop._vuer is None:
        return
    process = teleop._vuer.tv.process
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not process.is_alive():
            raise RuntimeError("Quest/Vuer server process exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"Quest/Vuer server did not listen on TCP port {port} within {timeout_s:.1f}s")


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or "unknown"


def _jsonable_dict(value: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, np.ndarray):
            result[key] = item.tolist()
        elif isinstance(item, np.generic):
            result[key] = item.item()
        else:
            result[key] = item
    return result


def _stream_front_camera(
    teleop: BiQuest3VR,
    frame: np.ndarray | None,
    recorder_state: RecorderState,
    elapsed_s: float | None,
    gesture_feedback: dict[str, object] | None = None,
    visual_notice: dict[str, object] | None = None,
) -> None:
    if frame is None or teleop._vuer is None or teleop._vuer.img_array is None:
        return
    destination = teleop._vuer.img_array
    height, stereo_width = destination.shape[:2]
    eye_width = stereo_width // 2
    if frame.shape[:2] != (height, eye_width):
        frame = cv2.resize(frame, (eye_width, height), interpolation=cv2.INTER_LINEAR)
    frame = frame.copy()
    progress: float | None = None
    if gesture_feedback and gesture_feedback.get("action"):
        action = str(gesture_feedback["action"])
        label = {
            "start": "STARTING RECORDING",
            "success": "MARKING SUCCESS",
            "failure": "MARKING FAILURE",
            "hide_video": "HOLD X+A: HIDE VIDEO",
            "show_video": "HOLD X+A: SHOW VIDEO",
        }[action]
        color = {
            "start": (40, 190, 255),
            "success": (75, 210, 95),
            "failure": (55, 55, 235),
            "hide_video": (230, 230, 230),
            "show_video": (230, 230, 230),
        }[action]
        progress = float(gesture_feedback.get("progress", 0.0))
    elif visual_notice:
        label = str(visual_notice["message"])
        color = {
            "recording": (55, 55, 235),
            "success": (75, 210, 95),
            "failure": (40, 190, 255),
            "error": (55, 55, 235),
            "info": (230, 230, 230),
        }.get(str(visual_notice.get("level", "info")), (230, 230, 230))
    elif recorder_state == RecorderState.RECORDING:
        label = f"REC {int(elapsed_s or 0) // 60:02d}:{int(elapsed_s or 0) % 60:02d}"
        color = (55, 55, 235)
    elif recorder_state == RecorderState.FINALIZING:
        label, color = "FINALIZING", (255, 190, 40)
    else:
        label, color = "READY", (70, 220, 140)

    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (eye_width, 68), (12, 14, 16), -1)
    cv2.addWeighted(overlay, 0.72, frame, 0.28, 0, frame)
    cv2.circle(frame, (28, 30), 10, color, -1)
    cv2.putText(frame, label, (48, 39), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2, cv2.LINE_AA)
    if progress is not None:
        progress = max(0.0, min(1.0, progress))
        cv2.rectangle(frame, (48, 50), (eye_width - 24, 58), (70, 74, 78), -1)
        cv2.rectangle(frame, (48, 50), (48 + int((eye_width - 72) * progress), 58), color, -1)
    destination[:, :eye_width] = frame
    destination[:, eye_width:] = frame


def _fit_tile(frame: np.ndarray | None, width: int, height: int, label: str) -> np.ndarray:
    tile = np.zeros((height, width, 3), dtype=np.uint8)
    if frame is not None and frame.ndim == 3:
        source_height, source_width = frame.shape[:2]
        scale = min(width / source_width, height / source_height)
        resized_width = max(1, int(source_width * scale))
        resized_height = max(1, int(source_height * scale))
        resized = cv2.resize(frame, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
        x = (width - resized_width) // 2
        y = (height - resized_height) // 2
        tile[y : y + resized_height, x : x + resized_width] = resized
    cv2.putText(tile, label, (12, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (245, 245, 245), 1, cv2.LINE_AA)
    return tile


def _compose_mosaic(cameras: SwitchableCameraManager, width: int, height: int) -> np.ndarray:
    front_height = max(1, int(height * 0.64))
    wrist_height = height - front_height
    left_width = width // 2
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:front_height] = _fit_tile(cameras.latest("cam_front"), width, front_height, "FRONT")
    canvas[front_height:, :left_width] = _fit_tile(
        cameras.latest("cam_left_wrist"), left_width, wrist_height, "LEFT WRIST"
    )
    canvas[front_height:, left_width:] = _fit_tile(
        cameras.latest("cam_right_wrist"), width - left_width, wrist_height, "RIGHT WRIST"
    )
    return canvas


def _stream_camera_mode(
    teleop: BiQuest3VR,
    cameras: SwitchableCameraManager,
    recorder_state: RecorderState,
    elapsed_s: float | None,
    gesture_feedback: dict[str, object] | None = None,
    visual_notice: dict[str, object] | None = None,
) -> None:
    if teleop._vuer is None or teleop._vuer.img_array is None:
        return
    destination = teleop._vuer.img_array
    height, stereo_width = destination.shape[:2]
    eye_width = stereo_width // 2
    if cameras.mode == CameraMode.OFF:
        destination.fill(0)
        return
    frame = _compose_mosaic(cameras, eye_width, height)
    _stream_front_camera(
        teleop,
        frame,
        recorder_state,
        elapsed_s,
        gesture_feedback=gesture_feedback,
        visual_notice=visual_notice,
    )


class VRRecordingGesture:
    """Long-press left-Y/right-B gestures with release-to-rearm protection."""

    def __init__(self, hold_s: float = 1.0):
        self.hold_s = hold_s
        self._candidate: str | None = None
        self._candidate_since = 0.0
        self._locked_until_release = False

    def update(self, left_state: list[float], right_state: list[float], state: RecorderState) -> str | None:
        # WebXR normalizes the left Y and right B face buttons to index 5.
        left_b = len(left_state) > 5 and float(left_state[5]) > 0.5
        right_b = len(right_state) > 5 and float(right_state[5]) > 0.5
        if self._locked_until_release:
            if not left_b and not right_b:
                self._locked_until_release = False
            return None

        candidate: str | None = None
        if state == RecorderState.IDLE and left_b and right_b:
            candidate = "start"
        elif state == RecorderState.RECORDING and right_b and not left_b:
            candidate = "success"
        elif state == RecorderState.RECORDING and left_b and not right_b:
            candidate = "failure"

        now = time.monotonic()
        if candidate != self._candidate:
            self._candidate = candidate
            self._candidate_since = now
            return None
        if candidate is not None and now - self._candidate_since >= self.hold_s:
            self._candidate = None
            self._locked_until_release = True
            return candidate
        return None

    def feedback(self) -> dict[str, object] | None:
        if self._candidate is None or self._locked_until_release:
            return None
        elapsed = max(0.0, time.monotonic() - self._candidate_since)
        return {
            "action": self._candidate,
            "progress": min(1.0, elapsed / self.hold_s) if self.hold_s > 0 else 1.0,
        }


class VRVideoDisplayGesture:
    """Toggle only the Quest video background with a long X+A chord."""

    def __init__(self, hold_s: float = 1.0):
        self.hold_s = hold_s
        self._candidate_since: float | None = None
        self._locked_until_release = False

    @staticmethod
    def _pressed(left_state: list[float], right_state: list[float]) -> bool:
        # WebXR normalizes left X and right A to face-button index 4.
        return (
            len(left_state) > 4
            and len(right_state) > 4
            and float(left_state[4]) > 0.5
            and float(right_state[4]) > 0.5
        )

    def update(self, left_state: list[float], right_state: list[float]) -> bool:
        pressed = self._pressed(left_state, right_state)
        if self._locked_until_release:
            if not pressed:
                self._locked_until_release = False
            return False
        if not pressed:
            self._candidate_since = None
            return False
        now = time.monotonic()
        if self._candidate_since is None:
            self._candidate_since = now
            return False
        if now - self._candidate_since >= self.hold_s:
            self._candidate_since = None
            self._locked_until_release = True
            return True
        return False

    def feedback(self, video_enabled: bool) -> dict[str, object] | None:
        if self._candidate_since is None or self._locked_until_release:
            return None
        elapsed = max(0.0, time.monotonic() - self._candidate_since)
        return {
            "action": "hide_video" if video_enabled else "show_video",
            "progress": min(1.0, elapsed / self.hold_s) if self.hold_s > 0 else 1.0,
        }


class RecordingCoordinator:
    def __init__(
        self,
        *,
        recorder: AsyncCanonicalRecorder,
        teleop: BiQuest3VR,
        cameras: SwitchableCameraManager,
        static_preflight: PreflightReport,
        code_commit: str,
        robot_id: str,
        scene_id: str,
        calibration_snapshot: dict[str, Any],
        capture_defaults: dict[str, Any],
        synchronized_camera_recorder: SynchronizedCameraRecorder | None = None,
    ):
        self.recorder = recorder
        self.teleop = teleop
        self.cameras = cameras
        self.synchronized_camera_recorder = synchronized_camera_recorder
        self.static_preflight = static_preflight
        self.code_commit = code_commit
        self.robot_id = robot_id
        self.scene_id = scene_id
        self.calibration_snapshot = calibration_snapshot
        self.capture_defaults = dict(capture_defaults)
        self.last_robot_feedback_ns = 0
        self.last_validation: dict[str, Any] | None = None
        self.last_outcome: dict[str, object] | None = None
        self.episode_started_ns: int | None = None
        self.runtime_failures: set[str] = set()
        self._last_health_event_ns = 0
        self._visual_notice: dict[str, object] | None = None
        self._episode_require_cameras = False
        self._last_task_id: str | None = None
        self._lock = threading.RLock()

    def set_visual_notice(self, message: str, level: str = "info", duration_s: float = 2.5) -> None:
        self._visual_notice = {
            "message": message,
            "level": level,
            "expires_ns": time.monotonic_ns() + int(duration_s * 1e9),
        }

    def visual_notice(self) -> dict[str, object] | None:
        notice = self._visual_notice
        if notice is None or time.monotonic_ns() >= int(notice["expires_ns"]):
            return None
        return notice

    def source_status(self) -> dict[str, dict[str, object]]:
        now_ns = time.monotonic_ns()
        sources = self.cameras.status()
        sources["camera_system"] = {
            "level": "PASS" if self.cameras.mode == CameraMode.MOSAIC else "WARN",
            "message": "three-camera mosaic" if self.cameras.mode == CameraMode.MOSAIC else "cameras physically closed",
        }
        if self.teleop.config.mock_vr:
            sources["quest3"] = {"level": "WARN", "message": "mock VR enabled"}
        elif self.teleop._vuer is None:
            sources["quest3"] = {"level": "BLOCKED", "message": "Vuer is unavailable"}
        else:
            event = self.teleop._vuer.controller_event_status
            age = event["age_s"]
            if age is None or age > 1.5:
                sources["quest3"] = {"level": "BLOCKED", "message": "no fresh controller event"}
            else:
                sources["quest3"] = {
                    "level": "PASS",
                    "message": f"event #{event['event_count']}, age {age:.2f}s",
                    "event_count": event["event_count"],
                    "event_age_s": age,
                }
        feedback_age = (now_ns - self.last_robot_feedback_ns) / 1e9 if self.last_robot_feedback_ns else None
        if feedback_age is None or feedback_age > 1.0:
            sources["robot_feedback"] = {"level": "BLOCKED", "message": "no fresh feedback"}
        else:
            sources["robot_feedback"] = {"level": "PASS", "message": f"age {feedback_age:.2f}s"}
        if self.recorder.status["writer_alive"]:
            sources["writer"] = {"level": "PASS", "message": "writer process alive"}
        else:
            sources["writer"] = {"level": "BLOCKED", "message": "writer process stopped"}
        sources["mcap_writer"] = {
            "level": "PASS" if self.recorder.enable_mcap else "WARN",
            "message": "raw MCAP sidecar enabled" if self.recorder.enable_mcap else "optional raw MCAP sidecar disabled",
        }
        recoverable = self.recorder.status["recoverable_episodes"]
        sources["recovery"] = {
            "level": "WARN" if recoverable else "PASS",
            "message": f"{len(recoverable)} preserved incomplete episode(s)" if recoverable else "no incomplete episodes",
        }
        return sources

    def combined_preflight(self) -> PreflightReport:
        checks = list(self.static_preflight.checks)
        sources = self.source_status()
        if self.cameras.mode == CameraMode.MOSAIC:
            for name in ("cam_front", "cam_left_wrist", "cam_right_wrist"):
                status = sources.get(name)
                if status is None:
                    checks.append(CheckResult(f"Camera {name}", "BLOCKED", "camera worker is missing"))
                else:
                    checks.append(CheckResult(f"Camera {name}", str(status["level"]), str(status["message"])))
        else:
            checks.append(CheckResult("Cameras", "WARN", "camera mode is OFF; visual VLA data will not be recorded"))
        for name in ("quest3", "robot_feedback", "writer"):
            status = sources[name]
            checks.append(CheckResult(name, str(status["level"]), str(status["message"])))
        return PreflightReport(tuple(checks))

    def status(self) -> dict[str, Any]:
        with self._lock:
            cleaning_ready = self.cleaning_ready_report(self.capture_defaults)
            return {
                "recorder": self.recorder.status,
                "preflight": cleaning_ready.to_dict(),
                "cleaning_ready": not cleaning_ready.blocked,
                "cleaning_ready_reason_codes": [
                    check.code for check in cleaning_ready.checks if check.level == "BLOCKED"
                ],
                "sources": self.source_status(),
                "last_validation": self.last_validation,
                "last_outcome": self.last_outcome,
                "camera_mode": self.cameras.mode.value,
                "capture_defaults": self.capture_defaults,
                "vla_annotation": {
                    "schema": ANNOTATION_SCHEMA,
                    "primitives": list(PRIMITIVES),
                    "arms": list(ARMS),
                },
            }

    def cleaning_ready_report(self, metadata: dict[str, Any]) -> PreflightReport:
        return validate_cleaning_ready(
            output_root=self.recorder.output_root,
            capture_contract_version=CAPTURE_CONTRACT_VERSION,
            expected_contract_version=CAPTURE_CONTRACT_VERSION,
            control_rate_hz=float(self.capture_defaults.get("control_rate_hz") or 0),
            camera_mode=self.cameras.mode.value,
            allow_no_cameras=bool(self.capture_defaults.get("allow_no_cameras")),
            calibration_status=str(self.calibration_snapshot.get("status") or "missing"),
            metadata={**self.capture_defaults, **metadata, "robot_id": self.robot_id, "scene_id": self.scene_id},
            base_report=self.combined_preflight(),
        )

    def set_camera_mode(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.recorder.record_session_event("dashboard_command", source="dashboard", payload={"command": "camera_mode", "request": payload})
            if self.recorder.state != RecorderState.IDLE:
                raise RuntimeError("camera mode can only change while the recorder is IDLE")
            mode = self.cameras.set_mode(str(payload.get("mode", "")))
            self.recorder.record_session_event("camera_mode_change", source="dashboard", payload={"camera_mode": mode.value})
            self.set_visual_notice("CAMERAS READY" if mode == CameraMode.MOSAIC else "CAMERAS OFF", "info")
            return {"camera_mode": mode.value}

    def add_language_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self.recorder.state != RecorderState.RECORDING:
                raise RuntimeError("language actions can only be added while recording")
            row: dict[str, Any] = normalize_language_action(payload)
            row.update({"host_monotonic_ns": time.monotonic_ns(), "host_wall_time_ns": time.time_ns()})
            self.recorder.record_row("language_action", row)
            self.set_visual_notice(f"ACTION: {row['primitive'].upper()}", "info", 1.5)
            return row

    def start_episode(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.recorder.record_session_event("dashboard_command", source="dashboard", payload={"command": "start"})
            preflight = self.cleaning_ready_report(payload)
            if preflight.blocked:
                failures = [item for item in preflight.checks if item.level == "BLOCKED"]
                codes = [item.code for item in failures]
                self.recorder.record_session_event("cleaning_ready_gate_failed", reason=";".join(codes), source="launcher", payload={"reason_codes": codes, "checks": [item.to_dict() for item in failures]})
                raise RuntimeError("cleaning-ready gate blocked: " + ", ".join(codes))
            operator_id = str(payload.get("operator_id", "")).strip()
            task_id = str(payload.get("task_id", "")).strip()
            language_instruction = normalize_english_text(
                payload.get("language_instruction"), "language_instruction"
            )
            if not operator_id or not task_id:
                raise ValueError("operator and task are required")
            if self._last_task_id is not None and task_id != self._last_task_id:
                self.recorder.record_session_event("task_change", reason="operator_selected_new_task", source="dashboard", payload={"previous_task_id": self._last_task_id, "task_id": task_id})
            episode_id = f"episode_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000:06d}"
            metadata = {
                "episode_id": episode_id,
                "operator_id": operator_id,
                "task_id": task_id,
                "task": language_instruction,
                "language_instruction": language_instruction,
                "annotation_schema": ANNOTATION_SCHEMA,
                "robot_id": self.robot_id,
                "scene_id": self.scene_id,
                "teleop_commit": self.code_commit,
                "control_commit": self.code_commit,
                "calibration_version": self.calibration_snapshot.get("calibration_version", "unknown"),
                "calibration_status": self.calibration_snapshot.get("status", "unknown"),
                "calibration_source": self.calibration_snapshot.get("source_file"),
                "robot_configuration": "dual_piper_6dof_gripper",
                "action_space": default_action_space_contract(),
                "collection_profile": collection_profile(
                    record_action_from_follower=bool(self.capture_defaults.get("record_action_from_follower")),
                    teleop_joint_alpha=float(self.capture_defaults.get("teleop_joint_alpha")),
                    teleop_gripper_alpha=float(self.capture_defaults.get("teleop_gripper_alpha")),
                ),
                "capture_mode": "cleaning_ready",
                "control_rate_hz": self.capture_defaults.get("control_rate_hz"),
                "camera_rate_hz": self.capture_defaults.get("camera_fps"),
                "action_semantics": {
                    "record_action_from_follower": self.capture_defaults.get("record_action_from_follower"),
                    "teleop_joint_alpha": self.capture_defaults.get("teleop_joint_alpha"),
                    "teleop_gripper_alpha": self.capture_defaults.get("teleop_gripper_alpha"),
                    "teleop.intent_t": "Quest3 controller pose/buttons before IK retargeting",
                    "controller.command_t": "joint/gripper command passed to robot.send_action",
                    "robot.executed_t": "joint/gripper feedback from robot.get_observation",
                    "policy.action_t": "derived downstream from configured canonical action_space",
                },
                "camera_mode": self.cameras.mode.value,
                "camera_config": list(self.cameras.workers),
                "preflight": preflight.to_dict(),
                "cleaning_ready": True,
                "cleaning_ready_reason_codes": [],
            }
            self.recorder.record_session_event(
                "cleaning_ready_gate_passed",
                source="launcher",
                payload={"camera_mode": self.cameras.mode.value, "capture_contract_version": CAPTURE_CONTRACT_VERSION},
            )
            self.recorder.start_episode(metadata)
            if self.synchronized_camera_recorder is not None:
                self.synchronized_camera_recorder.start_episode()
            self._last_task_id = task_id
            self.episode_started_ns = time.monotonic_ns()
            self._episode_require_cameras = self.cameras.mode == CameraMode.MOSAIC
            self.runtime_failures.clear()
            self.last_validation = None
            self.last_outcome = None
            self.set_visual_notice("RECORDING STARTED", "recording")
            return {"episode_id": episode_id}

    def finish_episode(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.recorder.record_session_event("dashboard_command", source="dashboard", payload={"command": "finish", "task_success": bool(payload.get("task_success"))})
            success = bool(payload.get("task_success"))
            reason = str(payload.get("failure_reason") or "task_failed")
            outcome = "SUCCESS" if success else "FAILURE"
            self.set_visual_notice(f"SAVING - MARKED {outcome}", "info", 10.0)
            if self.synchronized_camera_recorder is not None:
                self.synchronized_camera_recorder.stop_episode()
            path = self.recorder.finish_episode(
                task_success=success,
                failure_reason=reason,
                metadata_updates={"runtime_failures": sorted(self.runtime_failures)},
            )
            self.episode_started_ns = None
            report = validate_episode(path, require_cameras=self._episode_require_cameras)
            self.last_validation = report.to_dict()
            self.last_outcome = {
                "task_success": success,
                "failure_reason": reason,
                "path": str(path),
            }
            mcap_path = path / "raw.mcap"
            if mcap_path.is_file():
                mcap_report = validate_mcap(mcap_path, require_cameras=self._episode_require_cameras)
                (path / "mcap_validation.json").write_text(
                    json.dumps(mcap_report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                self.last_validation["mcap"] = mcap_report.to_dict()
            (path / "validation.json").write_text(
                json.dumps(self.last_validation, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            if report.valid:
                self.set_visual_notice(f"SAVED - {outcome} - VALID", "success" if success else "failure", 4.0)
            else:
                self.set_visual_notice("SAVED - VALIDATION FAILED", "error", 5.0)
            return {"path": str(path), "validation": self.last_validation}

    def abort_episode(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.recorder.record_session_event("dashboard_command", source="dashboard", payload={"command": "abort"})
            if self.synchronized_camera_recorder is not None:
                self.synchronized_camera_recorder.stop_episode()
            path = self.recorder.abort_episode(str(payload.get("reason") or "operator_abort"), source="dashboard")
            self.episode_started_ns = None
            self.last_outcome = {"aborted": True, "path": str(path)}
            self.set_visual_notice("ABORTED - NOT TRAINING READY", "error", 4.0)
            return {"path": str(path)}

    def reset_episode(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.recorder.record_session_event("dashboard_command", source="dashboard", payload={"command": "reset"})
            if self.synchronized_camera_recorder is not None:
                self.synchronized_camera_recorder.stop_episode()
            path = self.recorder.abort_episode(
                str(payload.get("reason") or "operator_reset"),
                source="dashboard", event_type="reset",
            )
            self.episode_started_ns = None
            self.last_outcome = {"aborted": True, "reset": True, "path": str(path)}
            self.set_visual_notice("RESET BOUNDARY - NOT TRAINING READY", "failure", 4.0)
            return {"path": str(path)}

    def monitor_runtime(self) -> None:
        if self.recorder.state != RecorderState.RECORDING:
            return
        failures = [
            f"{name}:{status['message']}"
            for name, status in self.source_status().items()
            if status["level"] == "BLOCKED"
        ]
        if not failures:
            return
        self.runtime_failures.update(failures)
        now_ns = time.monotonic_ns()
        if now_ns - self._last_health_event_ns >= 1_000_000_000:
            self.recorder.record_session_event("hardware_block", episode_id=self.recorder.status.get("episode_id"), reason="runtime_health_failure", source="health_monitor", payload={"failures": failures})
            self.recorder.record_row(
                "event",
                {
                    "host_monotonic_ns": now_ns,
                    "event": "runtime_health_failure",
                    "failures": failures,
                },
            )
            self._last_health_event_ns = now_ns


def main() -> int:
    args = parse_args()
    calibration_snapshot = _load_calibration(args.calibration_file)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    static_preflight = run_static_preflight(
        output_root=args.output_root,
        repo_root=REPO_ROOT,
        can_names=(args.left_can, args.right_can),
        minimum_free_gib=args.minimum_free_gib,
        mock_hardware=args.mock_hardware,
        require_estop=not args.allow_no_estop,
        tcp_ports=(8012, args.dashboard_port),
    )
    initial_camera_mode = CameraMode.OFF.value if args.allow_no_cameras else args.camera_mode
    launcher_gate = validate_cleaning_ready(
        output_root=args.output_root,
        capture_contract_version=CAPTURE_CONTRACT_VERSION,
        expected_contract_version=CAPTURE_CONTRACT_VERSION,
        control_rate_hz=args.fps,
        camera_mode=initial_camera_mode,
        allow_no_cameras=args.allow_no_cameras,
        calibration_status=str(calibration_snapshot.get("status") or "missing"),
        metadata={
            "operator_id": args.operator_id,
            "task_id": args.task_id,
            "language_instruction": args.language_instruction,
            "robot_id": args.robot_id,
            "scene_id": args.scene_id,
        },
        base_report=static_preflight,
    )
    for check in launcher_gate.checks:
        print(f"[{check.level}] {check.name}: {check.message}", flush=True)
    if launcher_gate.blocked:
        codes = [check.code for check in launcher_gate.checks if check.level == "BLOCKED"]
        failure_report = {
            "schema_version": "piper_cleaning_ready_gate_v1",
            "cleaning_ready": False,
            "reason_codes": codes,
            "checked_wall_time_ns": time.time_ns(),
            "report": launcher_gate.to_dict(),
        }
        report_path = args.output_root / f"cleaning_ready_gate_failure_{failure_report['checked_wall_time_ns']}.json"
        report_path.write_text(json.dumps(failure_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[BLOCKED] Cleaning-ready launcher gate failed ({', '.join(codes)}); hardware was not enabled.", flush=True)
        print(f"[BLOCKED] Diagnostic report: {report_path}", flush=True)
        return 2

    recorder = AsyncCanonicalRecorder(
        output_root=args.output_root,
        session_metadata={
            "started_wall_time_ns": time.time_ns(),
            "code_commit": _git_commit(),
            "robot_id": args.robot_id,
            "scene_id": args.scene_id,
            "mcap_enabled": args.enable_mcap,
            "calibration_version": calibration_snapshot.get("calibration_version", "unknown"),
        },
        camera_fps=args.camera_fps,
        enable_mcap=args.enable_mcap,
        mcap_jpeg_quality=args.mcap_jpeg_quality,
        calibration_snapshot=calibration_snapshot,
    )
    robot = BiPiperQuest3(
        BiPiperQuest3Config(
            left_can_name=args.left_can,
            right_can_name=args.right_can,
            cameras={},
            mock_hardware=args.mock_hardware,
        )
    )
    teleop: BiQuest3VR | None = None

    cameras: SwitchableCameraManager | None = None
    coordinator: RecordingCoordinator | None = None
    dashboard: OperatorDashboard | None = None
    robot_connected = False
    teleop_connected = False
    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False
        # Interrupt SDK/C-extension calls so cleanup can run instead of
        # waiting indefinitely inside the control loop.
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    try:
        teleop = BiQuest3VR(
            BiQuest3VRConfig(
                mock_vr=args.mock_vr,
                stream_camera_to_headset=True,
                enable_skeleton=False,
            )
        )
        teleop.connect()
        teleop_connected = True
        if not args.mock_vr:
            _wait_for_vuer_server(teleop)

        _connect_robot_with_timeout(robot, args.robot_connect_timeout)
        robot_connected = True

        camera_configs = _default_cameras()
        synchronized_camera_recorder = SynchronizedCameraRecorder(
            tuple(camera_configs.keys()),
            recorder.record_camera,
        )
        cameras = SwitchableCameraManager(
            lambda: make_cameras_from_configs(camera_configs),
            synchronized_camera_recorder,
        )
        cameras.set_mode(initial_camera_mode)

        coordinator = RecordingCoordinator(
            recorder=recorder,
            teleop=teleop,
            cameras=cameras,
            static_preflight=static_preflight,
            code_commit=_git_commit(),
            robot_id=args.robot_id,
            scene_id=args.scene_id,
            calibration_snapshot=calibration_snapshot,
            capture_defaults={
                "operator_id": args.operator_id,
                "task_id": args.task_id,
                "language_instruction": normalize_english_text(
                    args.language_instruction, "language_instruction"
                ),
                "record_action_from_follower": robot.config.record_action_from_follower,
                "teleop_joint_alpha": robot.config.teleop_joint_alpha,
                "teleop_gripper_alpha": robot.config.teleop_gripper_alpha,
                "control_rate_hz": args.fps,
                "allow_no_cameras": args.allow_no_cameras,
            },
            synchronized_camera_recorder=synchronized_camera_recorder,
        )
        dashboard = OperatorDashboard(
            host=args.dashboard_host,
            port=args.dashboard_port,
            get_status=coordinator.status,
            start_episode=coordinator.start_episode,
            finish_episode=coordinator.finish_episode,
            abort_episode=coordinator.abort_episode,
            set_camera_mode=coordinator.set_camera_mode,
            add_language_action=coordinator.add_language_action,
            reset_episode=coordinator.reset_episode,
        )
        dashboard.start()
        print(f"[READY] Operator dashboard: http://localhost:{args.dashboard_port}", flush=True)
        print("[READY] Enter Quest VR, wait for all checks to pass, then start the episode.", flush=True)

        period = 1.0 / args.fps
        started_at = time.monotonic()
        auto_started_at: float | None = None
        vr_gesture = VRRecordingGesture()
        vr_display_gesture = VRVideoDisplayGesture()
        last_health_check = 0.0
        last_system_metrics = 0.0
        recording_sample_index = 0
        current_recording_episode_id: str | None = None
        metrics_sampler = SystemMetricsSampler(args.output_root, (args.left_can, args.right_can))

        def finish_from_vr(success: bool) -> None:
            try:
                coordinator.finish_episode(
                    {"task_success": success, "failure_reason": "none" if success else "operator_marked_failure"}
                )
                print(f"[VR] Episode finalized as {'success' if success else 'failure'}.", flush=True)
            except Exception:
                logging.exception("VR finish gesture failed")

        while running:
            loop_started = time.monotonic()
            feedback_read_start_ns = time.monotonic_ns()
            observation = robot.get_observation()
            feedback_ns = time.monotonic_ns()
            coordinator.last_robot_feedback_ns = feedback_ns

            action = teleop.get_action()
            action_request_generated_ns = time.monotonic_ns()
            action_send_start_ns = time.monotonic_ns()
            sent_action = robot.send_action(action) or action
            action_send_end_ns = time.monotonic_ns()
            action_send_result_received_ns = time.monotonic_ns()
            command_ns = action_send_result_received_ns
            mode = teleop.mode
            vr_sample = teleop.last_vr_sample or {
                "host_monotonic_ns": command_ns,
                "left_pose_xyzw": [],
                "right_pose_xyzw": [],
                "left_state": [],
                "right_state": [],
                "event_status": {},
            }

            gesture = vr_gesture.update(vr_sample["left_state"], vr_sample["right_state"], recorder.state)
            if vr_display_gesture.update(vr_sample["left_state"], vr_sample["right_state"]):
                if teleop._vuer is not None:
                    video_enabled = not teleop._vuer.video_display_enabled
                    teleop._vuer.set_video_display_enabled(video_enabled)
                    coordinator.set_visual_notice(
                        "VR VIDEO ON" if video_enabled else "VR VIDEO HIDDEN",
                        "info",
                        2.5,
                    )
                    print(
                        f"[VR] Headset video display {'enabled' if video_enabled else 'hidden'}; "
                        "camera capture and recording remain active.",
                        flush=True,
                    )
            if gesture == "start":
                try:
                    coordinator.start_episode(
                        {
                            "operator_id": args.operator_id,
                            "task_id": args.task_id,
                            "language_instruction": args.language_instruction,
                        }
                    )
                    print("[VR] Episode recording started.", flush=True)
                except Exception as exc:
                    logging.warning("VR start gesture rejected: %s", exc)
                    coordinator.set_visual_notice("START BLOCKED", "error", 3.0)
            elif gesture in {"success", "failure"}:
                threading.Thread(
                    target=finish_from_vr,
                    args=(gesture == "success",),
                    name="vr-finalize",
                    daemon=True,
                ).start()

            if recorder.state == RecorderState.RECORDING:
                recorder_status = recorder.status
                episode_id = str(recorder_status.get("episode_id") or "episode")
                if episode_id != current_recording_episode_id:
                    current_recording_episode_id = episode_id
                    recording_sample_index = 0
                sample_id = f"{episode_id}:{recording_sample_index:06d}"
                sample_index = recording_sample_index
                recording_sample_index += 1
                request_payload = _jsonable_dict(action)
                sent_payload = _jsonable_dict(sent_action)
                observation_payload = _jsonable_dict(observation)
                event_status = dict(vr_sample.get("event_status") or {})
                vr_source_ns = int(vr_sample["host_monotonic_ns"])
                recorder.record_row(
                    "control",
                    {
                        "sample_id": sample_id,
                        "control_sample_index": sample_index,
                        "host_monotonic_ns": command_ns,
                        "host_wall_time_ns": time.time_ns(),
                        "source_timestamp_ns": command_ns,
                        "teleop_sample_host_monotonic_ns": vr_source_ns,
                        "robot_feedback_host_monotonic_ns": feedback_ns,
                        "action_request_generated_host_monotonic_ns": action_request_generated_ns,
                        "action_send_start_host_monotonic_ns": action_send_start_ns,
                        "action_send_end_host_monotonic_ns": action_send_end_ns,
                        "action_send_result_received_host_monotonic_ns": action_send_result_received_ns,
                        "left_mode": mode[0],
                        "right_mode": mode[1],
                        "phase": "teleop",
                        "action_requested": request_payload,
                        "action_sent": sent_payload,
                        "action_requested_json": json.dumps(request_payload, separators=(",", ":")),
                        "action_sent_json": json.dumps(sent_payload, separators=(",", ":")),
                        "safety_state": "normal",
                        **typed_control_fields(request_payload, sent_payload, teleop.last_control_processing),
                    },
                )
                recorder.record_row(
                    "robot_feedback",
                    {
                        "sample_id": sample_id,
                        "control_sample_index": sample_index,
                        "host_monotonic_ns": feedback_ns,
                        "host_wall_time_ns": time.time_ns(),
                        "source_timestamp_ns": feedback_ns,
                        "robot_feedback_source_timestamp_ns": None,
                        "robot_feedback_source_timestamp_unavailable_reason": "hardware_timestamp_unavailable",
                        "robot_feedback_read_start_host_monotonic_ns": feedback_read_start_ns,
                        "robot_feedback_host_receive_monotonic_ns": feedback_ns,
                        "robot_feedback_enqueue_host_monotonic_ns": time.monotonic_ns(),
                        "observation": observation_payload,
                        "observation_json": json.dumps(observation_payload, separators=(",", ":")),
                        **typed_feedback_fields(observation_payload, sent_payload),
                    },
                )
                recorder.record_row(
                    "vr_input",
                    {
                        "sample_id": sample_id,
                        "control_sample_index": sample_index,
                        "host_monotonic_ns": vr_source_ns,
                        "host_wall_time_ns": time.time_ns(),
                        "source_timestamp_ns": vr_source_ns,
                        "controller_event_count": event_status.get("event_count"),
                        "controller_last_event_ns": event_status.get("last_event_ns"),
                        "controller_event_source_timestamp_ns": None,
                        "controller_event_source_timestamp_unavailable_reason": "quest_device_timestamp_unavailable",
                        "controller_event_host_receive_monotonic_ns": event_status.get("last_event_ns") or None,
                        "controller_event_host_receive_unavailable_reason": None if event_status.get("last_event_ns") else "no_controller_event",
                        "controller_event_enqueue_host_monotonic_ns": time.monotonic_ns(),
                        "controller_event_age_s": event_status.get("age_s"),
                        "left_pose_xyzw": vr_sample["left_pose_xyzw"],
                        "right_pose_xyzw": vr_sample["right_pose_xyzw"],
                        "left_state": vr_sample["left_state"],
                        "right_state": vr_sample["right_state"],
                        "event_status": event_status,
                        "event_status_json": json.dumps(event_status, separators=(",", ":")),
                    },
                )

            if cameras is not None:
                elapsed_s = (
                    (time.monotonic_ns() - coordinator.episode_started_ns) / 1e9
                    if coordinator.episode_started_ns is not None
                    else None
                )
                _stream_camera_mode(
                    teleop,
                    cameras,
                    recorder.state,
                    elapsed_s,
                    gesture_feedback=(
                        vr_display_gesture.feedback(teleop._vuer.video_display_enabled)
                        if teleop._vuer is not None
                        else None
                    )
                    or vr_gesture.feedback(),
                    visual_notice=coordinator.visual_notice(),
                )

            if time.monotonic() - last_health_check >= 0.5:
                coordinator.monitor_runtime()
                last_health_check = time.monotonic()

            if recorder.state == RecorderState.RECORDING and time.monotonic() - last_system_metrics >= 1.0:
                recorder.record_mcap(
                    "/system/diagnostics",
                    metrics_sampler.sample(
                        loop_duration_s=time.monotonic() - loop_started,
                        target_period_s=period,
                        sources=coordinator.source_status(),
                        recorder_status=recorder.status,
                    ),
                )
                last_system_metrics = time.monotonic()

            if args.auto_start and auto_started_at is None and time.monotonic() - started_at > 0.5:
                coordinator.start_episode(
                    {
                        "operator_id": args.operator_id,
                        "task_id": args.task_id,
                        "language_instruction": args.language_instruction,
                    }
                )
                auto_started_at = time.monotonic()
            if auto_started_at is not None and args.duration > 0 and time.monotonic() - auto_started_at >= args.duration:
                coordinator.finish_episode({"task_success": True, "failure_reason": "none"})
                running = False

            remaining = period - (time.monotonic() - loop_started)
            if remaining > 0:
                time.sleep(remaining)
    except KeyboardInterrupt:
        print("[STOP] Shutdown requested.", flush=True)
    except TimeoutError as exc:
        logging.error("Hardware startup blocked: %s", exc)
        return 3
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        print("[STOP] Closing Canonical Raw recording system.", flush=True)
        if coordinator is not None and recorder.state == RecorderState.RECORDING:
            try:
                recorder.interrupt_episode("process_shutdown")
            except Exception:
                logging.exception("Failed to preserve active episode")
        if dashboard is not None:
            dashboard.close()
        if cameras is not None:
            cameras.close()
        if teleop_connected and teleop is not None:
            teleop.disconnect()
        if robot_connected:
            robot.disconnect()
        recorder.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
