#!/usr/bin/env python3
"""Low-risk Piper joint microstep characterization with mandatory interlocks."""

from __future__ import annotations

import argparse
import csv
import math
import subprocess
import time
from pathlib import Path

FACTOR = 1000.0 * 180.0 / math.pi
JOINT_FIELDS = tuple(f"joint_{i}" for i in range(1, 7))


def service_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", "piper-emergency-stop.service"],
        check=False,
    )
    return result.returncode == 0


def read_joints(piper) -> tuple[float, list[float]]:
    message = piper.GetArmJointMsgs()
    state = message.joint_state
    values = [getattr(state, name) / FACTOR for name in JOINT_FIELDS]
    if not all(math.isfinite(value) for value in values) or message.time_stamp <= 0:
        raise RuntimeError("invalid joint feedback")
    return float(message.time_stamp), values


def send_joints(piper, values: list[float], speed_percent: int) -> None:
    raw = [round(value * FACTOR) for value in values]
    piper.MotionCtrl_2(0x01, 0x01, speed_percent, 0x00)
    piper.JointCtrl(*raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--can", choices=("can_left", "can_right"), required=True)
    parser.add_argument("--joint", type=int, choices=range(1, 7), default=1)
    parser.add_argument("--delta-deg", type=float, default=0.5)
    parser.add_argument("--speed-percent", type=int, default=10)
    parser.add_argument("--ramp-s", type=float, default=1.0)
    parser.add_argument("--hold-s", type=float, default=0.5)
    parser.add_argument("--control-hz", type=float, default=50.0)
    parser.add_argument("--max-tracking-error-deg", type=float, default=3.0)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--armed", action="store_true")
    args = parser.parse_args()

    if not 0 < abs(args.delta_deg) <= 1.0:
        raise SystemExit("delta must be non-zero and <= 1 degree")
    if not 1 <= args.speed_percent <= 10:
        raise SystemExit("speed-percent must be in [1, 10]")
    if not service_active():
        raise SystemExit("E-stop service is not active")

    from piper_sdk import C_PiperInterface_V2

    piper = C_PiperInterface_V2(args.can, start_sdk_joint_limit=True)
    piper.ConnectPort()
    connected = True
    enabled = False
    try:
        time.sleep(0.5)
        feedback_ts, start = read_joints(piper)
        print(f"PREFLIGHT can={args.can} joint={args.joint} start_rad={start}")
        print(f"PREFLIGHT feedback_ts={feedback_ts:.6f} estop_service=active")
        if not args.armed:
            print("DRY_RUN_OK: no enable or motion command sent")
            return 0

        deadline = time.monotonic() + 3.0
        while not piper.EnablePiper():
            if time.monotonic() >= deadline:
                raise RuntimeError("enable timeout")
            time.sleep(0.02)
        enabled = True

        index = args.joint - 1
        delta = math.radians(args.delta_deg)
        target = start.copy()
        target[index] += delta
        period = 1.0 / args.control_hz
        steps = max(2, round(args.ramp_s * args.control_hz))
        max_error = math.radians(args.max_tracking_error_deg)
        args.log.parent.mkdir(parents=True, exist_ok=True)

        with args.log.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                ["host_monotonic_ns", "feedback_time_s", "phase"]
                + [f"command_joint_{i}_rad" for i in range(1, 7)]
                + [f"actual_joint_{i}_rad" for i in range(1, 7)]
                + [f"error_joint_{i}_rad" for i in range(1, 7)]
            )

            def cycle(phase: str, command: list[float]) -> None:
                if not service_active():
                    raise RuntimeError("E-stop service stopped")
                send_joints(piper, command, args.speed_percent)
                stamp, actual = read_joints(piper)
                errors = [expected - measured for expected, measured in zip(command, actual)]
                writer.writerow(
                    [time.monotonic_ns(), f"{stamp:.9f}", phase]
                    + [f"{value:.9f}" for value in command]
                    + [f"{value:.9f}" for value in actual]
                    + [f"{value:.9f}" for value in errors]
                )
                stream.flush()
                worst_index = max(range(6), key=lambda i: abs(errors[i]))
                if abs(errors[worst_index]) > max_error:
                    raise RuntimeError(
                        f"joint {worst_index + 1} tracking error "
                        f"{math.degrees(errors[worst_index]):.3f} deg exceeds limit"
                    )

            for step in range(1, steps + 1):
                begin = time.monotonic()
                alpha = step / steps
                command = [a + alpha * (b - a) for a, b in zip(start, target)]
                cycle("outbound", command)
                time.sleep(max(0.0, period - (time.monotonic() - begin)))
            for _ in range(max(1, round(args.hold_s * args.control_hz))):
                begin = time.monotonic()
                cycle("hold", target)
                time.sleep(max(0.0, period - (time.monotonic() - begin)))
            for step in range(1, steps + 1):
                begin = time.monotonic()
                alpha = step / steps
                command = [a + alpha * (b - a) for a, b in zip(target, start)]
                cycle("return", command)
                time.sleep(max(0.0, period - (time.monotonic() - begin)))

        _, final = read_joints(piper)
        print(f"MICROSTEP_OK can={args.can} joint={args.joint} final_error_deg={math.degrees(start[index]-final[index]):.4f} log={args.log}")
        return 0
    finally:
        if enabled:
            piper.DisableArm(7)
            print("ARM_DISABLED")
        if connected:
            piper.DisconnectPort()


if __name__ == "__main__":
    raise SystemExit(main())
