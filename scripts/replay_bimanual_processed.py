#!/usr/bin/env python3
"""Replay a processed dual-Piper episode on real or mock hardware."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import threading
import time

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lerobot_robot_bi_piper_quest3.bi_piper_quest3 import BiPiperQuest3
from lerobot_robot_bi_piper_quest3.config_bi_piper_quest3 import BiPiperQuest3Config
from replay.dataset import load_trajectory
from replay.player import TrajectoryPlayer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path, help="canonical dataset root")
    parser.add_argument("--episode", required=True, type=int)
    parser.add_argument("--left-can", default="can_left")
    parser.add_argument("--right-can", default="can_right")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--start-frame", type=int)
    parser.add_argument("--end-frame", type=int)
    parser.add_argument(
        "--action-source",
        choices=("controller.command_t", "action"),
        default="controller.command_t",
    )
    parser.add_argument("--mock-hardware", action="store_true")
    parser.add_argument("--hold-final-seconds", type=float, default=0.0)
    parser.add_argument("--yes", action="store_true", help="skip the real-hardware prompt")
    parser.add_argument("--report", type=Path, help="write playback summary JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    trajectory = load_trajectory(
        args.dataset,
        args.episode,
        action_source=args.action_source,
        start_frame=args.start_frame,
        end_frame=args.end_frame,
    )
    print(
        f"episode={trajectory.episode_index} frames={len(trajectory.frames)} "
        f"duration={trajectory.duration_s:.3f}s source={trajectory.action_source} "
        f"speed={args.speed:g}x"
    )
    if not args.mock_hardware and not args.yes:
        answer = input("Press Enter to connect and replay, or type 'no' to cancel: ").strip().lower()
        if answer in {"n", "no"}:
            return 0

    config = BiPiperQuest3Config(
        id="processed_replay",
        left_can_name=args.left_can,
        right_can_name=args.right_can,
        cameras={},
        mock_hardware=args.mock_hardware,
        teleop_joint_alpha=1.0,
        teleop_gripper_alpha=1.0,
    )
    robot = BiPiperQuest3(config)
    stop_event = threading.Event()
    result = None
    try:
        robot.connect()
        player = TrajectoryPlayer(robot)

        def progress(sent: int, frame_index: int) -> None:
            if sent == 1 or sent % 50 == 0 or sent == len(trajectory.frames):
                print(f"replay frame={frame_index} sent={sent}/{len(trajectory.frames)}")

        result = player.play(
            trajectory,
            speed=args.speed,
            stop_event=stop_event,
            on_frame=progress,
        )
        if args.hold_final_seconds > 0 and result.frames_sent:
            final_action = trajectory.frames[result.frames_sent - 1].as_action()
            deadline = time.monotonic() + args.hold_final_seconds
            period = 1.0 / max(trajectory.fps, 10.0)
            while time.monotonic() < deadline:
                robot.send_action(final_action)
                time.sleep(period)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nReplay interrupted.")
    finally:
        if robot.is_connected:
            robot.disconnect()

    if result is None:
        return 130 if stop_event.is_set() else 1
    summary = {
        "dataset": str(trajectory.dataset_root),
        "episode_index": result.episode_index,
        "action_source": trajectory.action_source,
        "speed": args.speed,
        "frames_sent": result.frames_sent,
        "first_frame": result.first_frame,
        "last_frame": result.last_frame,
        "trajectory_duration_s": result.trajectory_duration_s,
        "wall_duration_s": result.wall_duration_s,
        "stopped": result.stopped,
    }
    print(json.dumps(summary, indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
