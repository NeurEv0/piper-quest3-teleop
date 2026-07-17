#!/usr/bin/env python3
"""Grouped low/mid-frequency Piper sine characterization."""

from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path

from g1_grouped_microsteps import SAFE_POSES
from g1_joint_microstep import read_joints, send_joints, service_active


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--can", choices=tuple(SAFE_POSES), required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--amplitude-deg", type=float, default=0.3)
    parser.add_argument("--joints", default="1,2,3,4,5,6")
    parser.add_argument("--armed", action="store_true")
    args = parser.parse_args()
    if not args.armed or not service_active():
        raise SystemExit("requires --armed and active E-stop service")
    if not 0 < args.amplitude_deg <= 1.0:
        raise SystemExit("amplitude-deg must be in (0, 1]")
    joints = [int(value) - 1 for value in args.joints.split(",")]
    if not joints or any(joint not in range(6) for joint in joints):
        raise SystemExit("joints must be a comma-separated subset of 1..6")

    from piper_sdk import C_PiperInterface_V2

    hz = 50.0
    period = 1.0 / hz
    safe = SAFE_POSES[args.can]
    piper = C_PiperInterface_V2(args.can, start_sdk_joint_limit=True)
    piper.ConnectPort()
    enabled = False
    args.log.parent.mkdir(parents=True, exist_ok=True)
    try:
        time.sleep(0.5)
        _, current = read_joints(piper)
        deadline = time.monotonic() + 3.0
        while not piper.EnablePiper():
            if time.monotonic() > deadline:
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
                send_joints(piper, command, 5)
                stamp, actual = read_joints(piper)
                errors = [a - b for a, b in zip(command, actual)]
                writer.writerow(
                    [time.monotonic_ns(), f"{stamp:.9f}", phase]
                    + [f"{x:.9f}" for x in command + actual + errors]
                )
                stream.flush()
                worst = max(abs(x) for x in errors)
                if worst > math.radians(3.0):
                    raise RuntimeError(f"tracking error {math.degrees(worst):.3f} deg")

            def ramp(start: list[float], end: list[float], duration: float, phase: str) -> None:
                steps = round(duration * hz)
                for n in range(1, steps + 1):
                    tick = time.monotonic()
                    alpha = n / steps
                    sample(phase, [a + alpha * (b - a) for a, b in zip(start, end)])
                    time.sleep(max(0.0, period - (time.monotonic() - tick)))

            ramp(current, safe, 5.0, "recover_safe")
            amplitude = math.radians(args.amplitude_deg)
            for joint in joints:
                for frequency in (0.2, 0.5):
                    duration = 1.0 / frequency
                    steps = round(duration * hz)
                    for n in range(steps + 1):
                        tick = time.monotonic()
                        command = safe.copy()
                        command[joint] += amplitude * math.sin(2.0 * math.pi * frequency * n / hz)
                        sample(f"j{joint+1}_sine_{frequency:.1f}hz", command)
                        time.sleep(max(0.0, period - (time.monotonic() - tick)))
                    ramp(command, safe, 0.2, f"j{joint+1}_settle")

        _, final = read_joints(piper)
        print("SINE_OK", args.can, [round(math.degrees(a-b), 4) for a,b in zip(safe,final)])
        return 0
    finally:
        if enabled:
            piper.DisableArm(7)
            print("ARM_DISABLED")
        piper.DisconnectPort()


if __name__ == "__main__":
    raise SystemExit(main())
