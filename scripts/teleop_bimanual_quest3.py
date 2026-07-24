#!/usr/bin/env python3
"""Real-time Quest3 teleoperation for two Piper arms without recording."""

from __future__ import annotations

import argparse
import logging
import signal
import time

import cv2
import numpy as np

from lerobot.cameras.utils import make_cameras_from_configs
from lerobot_robot_bi_piper_quest3.bi_piper_quest3 import BiPiperQuest3
from lerobot_robot_bi_piper_quest3.config_bi_piper_quest3 import (
    BiPiperQuest3Config,
    _default_cameras,
)
from lerobot_teleoperator_bi_quest3_vr.bi_quest3_vr import BiQuest3VR
from lerobot_teleoperator_bi_quest3_vr.config_bi_quest3_vr import BiQuest3VRConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left-can", default="can_left")
    parser.add_argument("--right-can", default="can_right")
    parser.add_argument("--fps", type=float, default=30.0)
    return parser.parse_args()


def stream_front_camera(teleop: BiQuest3VR, frame: np.ndarray | None) -> None:
    """Copy the front RGB camera into the side-by-side Quest display buffer."""
    if teleop._vuer is None or teleop._vuer.img_array is None:
        return
    if frame is None:
        return

    destination = teleop._vuer.img_array
    height, stereo_width = destination.shape[:2]
    eye_width = stereo_width // 2
    frame = np.asarray(frame, dtype=np.uint8)
    if frame.shape[:2] != (height, eye_width):
        frame = cv2.resize(frame, (eye_width, height), interpolation=cv2.INTER_LINEAR)
    destination[:, :eye_width] = frame
    destination[:, eye_width:] = frame


def feedback_line(observation: dict, modes: tuple[str, str]) -> str:
    left = [float(observation[f"left_joint_{i}.pos"]) for i in range(1, 7)]
    right = [float(observation[f"right_joint_{i}.pos"]) for i in range(1, 7)]
    return (
        f"[STATE] modes={modes} "
        f"left={np.round(left, 3).tolist()} right={np.round(right, 3).tolist()}"
    )


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    robot = BiPiperQuest3(
        BiPiperQuest3Config(
            left_can_name=args.left_can,
            right_can_name=args.right_can,
            cameras={},
        )
    )
    teleop = BiQuest3VR(
        BiQuest3VRConfig(
            mock_vr=False,
            stream_camera_to_headset=True,
            enable_skeleton=False,
        )
    )
    front_camera = make_cameras_from_configs(
        {"cam_front": _default_cameras()["cam_front"]}
    )["cam_front"]

    running = True

    def stop(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    period = 1.0 / args.fps
    last_report = 0.0
    robot_connected = False
    teleop_connected = False
    camera_connected = False
    last_camera_warning = 0.0
    try:
        front_camera.connect()
        camera_connected = True
        robot.connect()
        robot_connected = True
        teleop.connect()
        teleop_connected = True
        print("[READY] Dual-arm teleoperation active: left controller -> left arm, right -> right.", flush=True)

        while running:
            started = time.monotonic()
            observation = robot.get_observation()
            try:
                frame = front_camera.async_read(timeout_ms=100)
            except (RuntimeError, TimeoutError) as exc:
                frame = None
                if started - last_camera_warning >= 5.0:
                    logging.warning("Front RGB camera frame unavailable: %s", exc)
                    last_camera_warning = started
            stream_front_camera(teleop, frame)
            action = teleop.get_action()
            robot.send_action(action)

            if started - last_report >= 1.0:
                print(feedback_line(observation, teleop.mode), flush=True)
                last_report = started

            remaining = period - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
    finally:
        print("[STOP] Shutting down dual-arm teleoperation.", flush=True)
        if teleop_connected:
            teleop.disconnect()
        if camera_connected:
            front_camera.disconnect()
        if robot_connected:
            robot.disconnect()


if __name__ == "__main__":
    main()
