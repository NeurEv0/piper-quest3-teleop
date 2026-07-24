#!/usr/bin/env python3
"""No-hardware integration tests for Canonical Raw recording."""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import time
import urllib.request
from multiprocessing import Array, Value
from pathlib import Path
from types import SimpleNamespace

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from canonical_raw.preflight import run_static_preflight
from canonical_raw.cameras import CameraMode, SwitchableCameraManager
from canonical_raw.dashboard import OperatorDashboard
from canonical_raw.recorder import AsyncCanonicalRecorder, RecorderState
from canonical_raw.validator import validate_episode
from canonical_raw.vla_annotations import normalize_english_text, normalize_language_action
from scripts.record_bimanual_canonical import (
    VRRecordingGesture,
    VRVideoDisplayGesture,
    _compose_mosaic,
    _stream_front_camera,
)
from teleop.TeleVision import OpenTeleVision, black_passthrough_mask
from teleop.VuerTeleop import VuerTeleop
from lerobot_robot_bi_piper_quest3.bi_piper_quest3 import BiPiperQuest3, _quintic_blend


def test_canonical_raw_round_trip() -> None:
    with tempfile.TemporaryDirectory(prefix="canonical_raw_test_") as temp:
        root = Path(temp)
        recorder = AsyncCanonicalRecorder(
            output_root=root,
            session_metadata={"test": True},
            camera_fps=15.0,
        )
        try:
            episode_id = recorder.start_episode(
                {
                    "episode_id": "episode_test_000001",
                    "operator_id": "test_operator",
                    "task_id": "test_task",
                    "camera_mode": "mosaic",
                    "language_instruction": "Move the test object to the target location.",
                }
            )
            assert episode_id == "episode_test_000001"
            assert recorder.state == RecorderState.RECORDING

            rng = np.random.default_rng(7)
            previous = time.monotonic_ns()
            for index in range(30):
                now = max(time.monotonic_ns(), previous + 1)
                previous = now
                recorder.record_row(
                    "control",
                    {
                        "host_monotonic_ns": now,
                        "host_wall_time_ns": time.time_ns(),
                        "left_mode": "TELEOP",
                        "right_mode": "TELEOP",
                        "action_requested_json": "{}",
                        "action_sent_json": "{}",
                    },
                )
                recorder.record_row(
                    "robot_feedback",
                    {
                        "host_monotonic_ns": now,
                        "host_wall_time_ns": time.time_ns(),
                        "observation_json": "{}",
                    },
                )
                recorder.record_row(
                    "vr_input",
                    {
                        "host_monotonic_ns": now,
                        "host_wall_time_ns": time.time_ns(),
                        "left_pose_xyzw": [0.0] * 7,
                        "right_pose_xyzw": [0.0] * 7,
                    },
                )
                for camera_name in ("cam_front", "cam_left_wrist", "cam_right_wrist"):
                    frame = rng.integers(0, 256, size=(120, 160, 3), dtype=np.uint8)
                    assert recorder.record_camera(camera_name, frame, now)

            episode_path = recorder.finish_episode(task_success=True, failure_reason="none")
            assert episode_path.name == "episode_test_000001"
            assert not episode_path.with_name(episode_path.name + ".inprogress").exists()
            assert recorder.state == RecorderState.IDLE
            report = validate_episode(episode_path)
            assert report.valid, report.errors
            metadata = json.loads((episode_path / "metadata.json").read_text())
            assert metadata["task_success"] is True
            assert metadata["recording_state"] == "finalized"
            assert metadata["mcap_log"]["status"] == "disabled"
            assert not (episode_path / "raw.mcap").exists()
            assert len(list(episode_path.glob("camera_*.mp4"))) == 3
        finally:
            recorder.close()


def test_camera_off_episode_is_valid_without_video() -> None:
    with tempfile.TemporaryDirectory(prefix="canonical_camera_off_") as temp:
        recorder = AsyncCanonicalRecorder(output_root=Path(temp), session_metadata={"test": True})
        try:
            recorder.start_episode(
                {
                    "episode_id": "episode_camera_off",
                    "operator_id": "test_operator",
                    "task_id": "control_only",
                    "camera_mode": "off",
                    "language_instruction": "Hold both robot arms at their current positions.",
                }
            )
            now = time.monotonic_ns()
            wall = time.time_ns()
            for kind in ("control", "robot_feedback", "vr_input"):
                recorder.record_row(kind, {"host_monotonic_ns": now, "host_wall_time_ns": wall})
            path = recorder.finish_episode(task_success=True, failure_reason="none")
            report = validate_episode(path, require_cameras=False)
            assert report.valid, report.errors
            assert not list(path.glob("camera_*.mp4"))
        finally:
            recorder.close()


def test_vla_english_contract() -> None:
    assert normalize_english_text("  Pick up   the red cup. ", "instruction") == "Pick up the red cup."
    action = normalize_language_action(
        {
            "primitive": "grasp",
            "arm": "right",
            "language_action": "Grasp the red cup with the right gripper.",
            "object": "the red cup",
            "target": "the tray",
        }
    )
    assert action["primitive"] == "grasp"
    try:
        normalize_english_text("抓住红色杯子", "instruction")
    except ValueError:
        pass
    else:
        raise AssertionError("Chinese instructions must not enter the English VLA field")


def test_three_camera_mosaic_layout() -> None:
    frames = {
        "cam_front": np.full((40, 60, 3), (220, 30, 30), dtype=np.uint8),
        "cam_left_wrist": np.full((40, 60, 3), (30, 220, 30), dtype=np.uint8),
        "cam_right_wrist": np.full((40, 60, 3), (30, 30, 220), dtype=np.uint8),
    }
    cameras = SimpleNamespace(latest=lambda name: frames[name])
    mosaic = _compose_mosaic(cameras, 200, 120)
    assert mosaic.shape == (120, 200, 3)
    assert mosaic[50, 100, 0] > mosaic[50, 100, 1]
    assert mosaic[100, 50, 1] > mosaic[100, 50, 0]
    assert mosaic[100, 150, 2] > mosaic[100, 150, 0]


def test_camera_off_releases_devices() -> None:
    class FakeCamera:
        def __init__(self) -> None:
            self.connected = False
            self.disconnected = False

        def connect(self) -> None:
            self.connected = True

        def async_read(self, timeout_ms: int) -> np.ndarray:
            time.sleep(0.002)
            return np.zeros((24, 32, 3), dtype=np.uint8)

        def disconnect(self) -> None:
            self.disconnected = True

    devices = {name: FakeCamera() for name in ("cam_front", "cam_left_wrist", "cam_right_wrist")}
    manager = SwitchableCameraManager(lambda: devices, lambda *_args: None)
    manager.set_mode(CameraMode.MOSAIC)
    deadline = time.monotonic() + 1.0
    while not all(device.connected for device in devices.values()) and time.monotonic() < deadline:
        time.sleep(0.01)
    manager.set_mode(CameraMode.OFF)
    assert all(device.disconnected for device in devices.values())
    assert manager.workers == {}


def test_dashboard_exposes_camera_modes_and_vla_actions() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def callback(name: str):
        def invoke(payload: dict[str, object]) -> dict[str, object]:
            calls.append((name, payload))
            return {"ok": True, **payload}

        return invoke

    dashboard = OperatorDashboard(
        host="127.0.0.1",
        port=0,
        get_status=lambda: {"recorder": {"state": "IDLE"}},
        start_episode=callback("start"),
        finish_episode=callback("finish"),
        abort_episode=callback("abort"),
        set_camera_mode=callback("camera"),
        add_language_action=callback("language_action"),
    )
    dashboard.start()
    try:
        base = f"http://127.0.0.1:{dashboard._server.server_port}"
        html = urllib.request.urlopen(base, timeout=2).read().decode()
        assert "CAMERAS OFF" in html
        assert "3-CAMERA MOSAIC" in html
        assert "Language instruction (English)" in html
        payload = json.dumps({"mode": "off"}).encode()
        request = urllib.request.Request(
            base + "/api/camera-mode", data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        assert json.loads(urllib.request.urlopen(request, timeout=2).read())["mode"] == "off"
        assert calls[-1] == ("camera", {"mode": "off"})
    finally:
        dashboard.close()


def test_static_preflight_mock_mode() -> None:
    with tempfile.TemporaryDirectory(prefix="canonical_preflight_test_") as temp:
        report = run_static_preflight(
            output_root=Path(temp),
            repo_root=REPO_ROOT,
            can_names=("missing_left", "missing_right"),
            minimum_free_gib=0.0,
            mock_hardware=True,
            require_estop=False,
        )
        assert not report.blocked
        assert any(item.level == "WARN" for item in report.checks)


def test_static_preflight_blocks_occupied_tcp_port() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="canonical_preflight_port_test_") as temp:
            report = run_static_preflight(
                output_root=Path(temp),
                repo_root=REPO_ROOT,
                can_names=("missing_left", "missing_right"),
                minimum_free_gib=0.0,
                mock_hardware=True,
                require_estop=False,
                tcp_ports=(port,),
            )
        assert report.blocked
        assert any(item.name == f"TCP port {port}" and item.level == "BLOCKED" for item in report.checks)
    finally:
        listener.close()


def test_vr_recording_gestures_and_overlay() -> None:
    gesture = VRRecordingGesture(hold_s=0.0)
    left = [0.0] * 14
    right = [0.0] * 14

    left[5] = right[5] = 1.0
    assert gesture.update(left, right, RecorderState.IDLE) is None
    assert gesture.update(left, right, RecorderState.IDLE) == "start"
    left[5] = right[5] = 0.0
    gesture.update(left, right, RecorderState.RECORDING)

    right[5] = 1.0
    assert gesture.update(left, right, RecorderState.RECORDING) is None
    assert gesture.update(left, right, RecorderState.RECORDING) == "success"
    right[5] = 0.0
    gesture.update(left, right, RecorderState.RECORDING)

    left[5] = 1.0
    assert gesture.update(left, right, RecorderState.RECORDING) is None
    assert gesture.update(left, right, RecorderState.RECORDING) == "failure"

    image_buffer = np.zeros((120, 320, 3), dtype=np.uint8)
    teleop = SimpleNamespace(_vuer=SimpleNamespace(img_array=image_buffer))
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    _stream_front_camera(
        teleop,
        frame,
        RecorderState.IDLE,
        None,
        gesture_feedback={"action": "start", "progress": 0.5},
    )
    assert np.any(image_buffer)
    assert np.array_equal(image_buffer[:, :160], image_buffer[:, 160:])


def test_vr_video_display_gesture_requires_hold_and_release() -> None:
    gesture = VRVideoDisplayGesture(hold_s=0.0)
    left = [0.0] * 14
    right = [0.0] * 14

    left[4] = right[4] = 1.0
    assert gesture.update(left, right) is False
    assert gesture.feedback(video_enabled=True)["action"] == "hide_video"
    assert gesture.update(left, right) is True
    assert gesture.update(left, right) is False

    left[4] = right[4] = 0.0
    assert gesture.update(left, right) is False
    left[4] = right[4] = 1.0
    assert gesture.update(left, right) is False
    assert gesture.feedback(video_enabled=False)["action"] == "show_video"
    assert gesture.update(left, right) is True


def test_video_display_flag_is_independent_from_stream_capture() -> None:
    television = OpenTeleVision.__new__(OpenTeleVision)
    television._stream_images = True
    television.video_display_enabled_shared = Value("b", True, lock=True)

    television.set_video_display_enabled(False)
    assert television.video_display_enabled is False
    assert television._stream_images is True
    television.set_video_display_enabled(True)
    assert television.video_display_enabled is True


def test_controller_pose_tolerates_small_rotation_drift() -> None:
    pose_matrix = np.eye(4)
    pose_matrix[:3, :3] = np.array(
        [
            [1.00000108, -0.00000018, 0.00000013],
            [0.0, 1.00000042, 0.00000101],
            [0.0, 0.0, 1.00000070],
        ]
    )
    pose_matrix[:3, 3] = [0.1, -0.2, 0.3]

    pose = VuerTeleop._mat_to_pose7(pose_matrix)

    assert np.allclose(pose[:3], [0.1, -0.2, 0.3])
    assert np.isclose(np.linalg.norm(pose[3:]), 1.0)
    assert np.all(np.isfinite(pose))


def test_video_mode_uses_an_opaque_black_passthrough_mask() -> None:
    mask = black_passthrough_mask()
    assert mask.key == "black-environment"
    assert mask.material["color"] == "#000000"
    assert mask.material["side"] == 1
    assert mask.args[0] == 20


def test_shutdown_quintic_blend_is_smooth_and_monotonic() -> None:
    samples = np.array([_quintic_blend(value) for value in np.linspace(0.0, 1.0, 1001)])
    assert samples[0] == 0.0
    assert samples[-1] == 1.0
    assert np.all(np.diff(samples) >= 0.0)
    assert np.isclose(_quintic_blend(-1.0), 0.0)
    assert np.isclose(_quintic_blend(2.0), 1.0)


def test_shutdown_park_moves_to_captured_pose_without_zero_command() -> None:
    robot = BiPiperQuest3.__new__(BiPiperQuest3)
    robot.config = SimpleNamespace(
        shutdown_min_duration_s=0.01,
        shutdown_max_duration_s=1.0,
        shutdown_max_joint_speed_rad_s=100.0,
        shutdown_max_gripper_speed_m_s=100.0,
        shutdown_rate_hz=200.0,
        shutdown_settle_s=0.0,
        shutdown_joint_tolerance_rad=1e-6,
        shutdown_gripper_tolerance_m=1e-6,
    )
    state = {
        f"{side}_{motor}.pos": (0.4 if "joint" in motor else 0.05)
        for side in ("left", "right")
        for motor in ("joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "gripper")
    }
    target = {
        key: (value + 0.2 if "joint" in key else 0.03)
        for key, value in state.items()
    }
    robot._disabled_start_pose = target
    sent: list[dict[str, float]] = []
    robot.get_record_action_from_follower = lambda: dict(state)

    def send_action(action: dict[str, float]) -> dict[str, float]:
        sent.append(dict(action))
        state.update(action)
        return action

    robot.send_action = send_action

    assert robot._park_for_disable() is True
    assert sent
    assert sent[-1] == target
    assert all(any(abs(value) > 1e-6 for value in action.values()) for action in sent)


def test_shutdown_pose_rejects_implausible_feedback() -> None:
    pose = {
        f"{side}_{motor}.pos": (0.4 if "joint" in motor else 0.05)
        for side in ("left", "right")
        for motor in ("joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6", "gripper")
    }
    assert BiPiperQuest3._pose_is_finite(pose)
    assert BiPiperQuest3._pose_is_plausible(pose)
    pose["left_joint_1.pos"] = 99.0
    assert not BiPiperQuest3._pose_is_plausible(pose)


def test_controller_event_updates_face_buttons_without_debug_failure() -> None:
    television = OpenTeleVision.__new__(OpenTeleVision)
    television.controller_event_count = Value("q", 0, lock=True)
    television.last_controller_event_ns = Value("q", 0, lock=True)
    television.right_controller_shared = Array("d", 16, lock=True)
    television.left_controller_shared = Array("d", 16, lock=True)
    television.right_state_shared = Array("d", 14, lock=True)
    television.left_state_shared = Array("d", 14, lock=True)
    event = SimpleNamespace(
        value={
            "right": [0.0] * 16,
            "left": [0.0] * 16,
            "rightState": {"bButton": True, "squeeze": True},
            "leftState": {"bButton": True, "squeeze": False},
        }
    )

    asyncio.run(television.on_controller_move(event, None))

    assert television.controller_event_count.value == 1
    assert television.right_state_shared[5] == 1.0
    assert television.left_state_shared[5] == 1.0
