#!/usr/bin/env python3
"""Grouped six-joint Piper microsteps with one enable cycle and full-state logging."""

from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

from g1_joint_microstep import FACTOR, read_joints, send_joints, service_active

SAFE_POSES = {
    "can_left": [0.0, 0.0, 0.0, 0.0, 0.4967556117, 0.0],
    "can_right": [-0.0009599311, 0.0, 0.0, 0.0074351026, 0.5028817174, 0.0],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--can", choices=tuple(SAFE_POSES), required=True)
    parser.add_argument("--delta-deg", type=float, default=0.3)
    parser.add_argument("--control-hz", type=float, default=50.0)
    parser.add_argument("--recovery-s", type=float, default=5.0)
    parser.add_argument("--ramp-s", type=float, default=0.8)
    parser.add_argument("--hold-s", type=float, default=0.2)
    parser.add_argument("--speed-percent", type=int, default=5)
    parser.add_argument("--max-error-deg", type=float, default=3.0)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--armed", action="store_true")
    args = parser.parse_args()
    if not args.armed:
        raise SystemExit("refusing motion without --armed")
    if not service_active():
        raise SystemExit("E-stop service is not active")
    if not 0 < args.delta_deg <= 0.5 or not 1 <= args.speed_percent <= 10:
        raise SystemExit("unsafe delta or speed")

    from piper_sdk import C_PiperInterface_V2

    piper = C_PiperInterface_V2(args.can, start_sdk_joint_limit=True)
    piper.ConnectPort()
    enabled = False
    safe = SAFE_POSES[args.can]
    period = 1.0 / args.control_hz
    max_error = math.radians(args.max_error_deg)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    try:
        time.sleep(0.5)
        _, current = read_joints(piper)
        print("CURRENT_DEG", [round(math.degrees(x), 3) for x in current])
        deadline = time.monotonic() + 3.0
        while not piper.EnablePiper():
            if time.monotonic() >= deadline:
                raise RuntimeError("enable timeout")
            time.sleep(0.02)
        enabled = True

        with args.log.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(
                ["host_monotonic_ns", "feedback_time_s", "phase"]
                + [f"command_joint_{i}_rad" for i in range(1, 7)]
                + [f"actual_joint_{i}_rad" for i in range(1, 7)]
                + [f"error_joint_{i}_rad" for i in range(1, 7)]
            )

            def sample(phase: str, command: list[float]) -> None:
                if not service_active():
                    raise RuntimeError("E-stop service stopped")
                send_joints(piper, command, args.speed_percent)
                stamp, actual = read_joints(piper)
                errors = [a - b for a, b in zip(command, actual)]
                writer.writerow(
                    [time.monotonic_ns(), f"{stamp:.9f}", phase]
                    + [f"{x:.9f}" for x in command + actual + errors]
                )
                stream.flush()
                worst = max(range(6), key=lambda i: abs(errors[i]))
                if abs(errors[worst]) > max_error:
                    raise RuntimeError(
                        f"joint {worst + 1} error {math.degrees(errors[worst]):.3f} deg"
                    )

            def ramp(phase: str, start: list[float], end: list[float], duration: float) -> None:
                steps = max(2, round(duration * args.control_hz))
                for step in range(1, steps + 1):
                    begin = time.monotonic()
                    alpha = step / steps
                    sample(phase, [a + alpha * (b - a) for a, b in zip(start, end)])
                    time.sleep(max(0.0, period - (time.monotonic() - begin)))

            ramp("recover_safe", current, safe, args.recovery_s)
            for joint in range(6):
                for direction in (1.0, -1.0):
                    target = safe.copy()
                    target[joint] += direction * math.radians(args.delta_deg)
                    label = f"j{joint + 1}_{'pos' if direction > 0 else 'neg'}"
                    ramp(label + "_out", safe, target, args.ramp_s)
                    for _ in range(max(1, round(args.hold_s * args.control_hz))):
                        sample(label + "_hold", target)
                        time.sleep(period)
                    ramp(label + "_return", target, safe, args.ramp_s)

        _, final = read_joints(piper)
        print("GROUPED_OK", args.can, "final_error_deg", [round(math.degrees(a-b), 4) for a,b in zip(safe,final)])
        return 0
    finally:
        if enabled:
            piper.DisableArm(7)
            print("ARM_DISABLED")
        piper.DisconnectPort()


if __name__ == "__main__":
    raise SystemExit(main())
