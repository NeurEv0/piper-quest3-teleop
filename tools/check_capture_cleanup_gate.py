#!/usr/bin/env python3
"""Capture-side cleanup acceptance gate."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_ROOT = "/home/ylhp-e-ai/ZHITAI_1t/TELEOP/piper_canonical_raw"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def _check(condition: bool, message: str, failures: list[str]) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {message}")
    if not condition:
        failures.append(message)


def _read_text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _active_text_files() -> list[Path]:
    roots = [
        "canonical_raw",
        "mcap_log",
        "scripts",
        "tests",
        "tools",
        "lerobot_robot_bi_piper_quest3",
        "lerobot_teleoperator_bi_quest3_vr",
    ]
    suffixes = {".py", ".sh", ".md", ".json", ".txt", ".yaml", ".yml"}
    files: list[Path] = []
    for root in roots:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in suffixes and path.name != "check_capture_cleanup_gate.py":
                files.append(path)
    return files


def main() -> int:
    failures: list[str] = []

    for path in [
        "scripts/start_vla_capture.sh",
        "scripts/stop_vla_capture.sh",
        "scripts/run_bimanual_mcap_shadow.sh",
        "scripts/run_bimanual_canonical.sh",
        "scripts/record_bimanual_canonical.py",
    ]:
        _check((REPO_ROOT / path).is_file(), f"{path} exists", failures)

    for path in [
        "lerobot_robot_piper_quest3",
        "lerobot_teleoperator_quest3_vr",
        "tests/test_mock_recording.py",
        "backup",
        "Log",
        "MUJOCO_LOG.TXT",
    ]:
        _check(not (REPO_ROOT / path).exists(), f"{path} is absent", failures)

    artifacts = []
    for path in REPO_ROOT.rglob("*"):
        if "__pycache__" in path.parts:
            artifacts.append(path)
        elif path.is_file() and (
            path.suffix in {".pyc", ".pyo"} or path.name.endswith("~") or ".bak" in path.name
        ):
            artifacts.append(path)
    _check(not artifacts, "no backup/cache artifacts remain", failures)

    start = _run(["bash", "scripts/start_vla_capture.sh", "--task", "1", "--dry-run"])
    _check(start.returncode == 0, "start_vla_capture.sh --dry-run exits 0", failures)
    _check("run_bimanual_mcap_shadow.sh" in start.stdout, "dry-run enters MCAP shadow wrapper", failures)
    _check("--task-id stack_white_on_mint_green" in start.stdout, "dry-run resolves task 1", failures)

    for script in [
        "scripts/start_vla_capture.sh",
        "scripts/stop_vla_capture.sh",
        "scripts/run_bimanual_canonical.sh",
        "scripts/run_bimanual_mcap_shadow.sh",
    ]:
        result = _run(["bash", "-n", script])
        _check(result.returncode == 0, f"{script} passes bash -n", failures)

    canonical_shell = _read_text("scripts/run_bimanual_canonical.sh")
    canonical_py = _read_text("scripts/record_bimanual_canonical.py")
    _check(DEFAULT_OUTPUT_ROOT in canonical_shell, "shell launcher default output root is TELEOP/piper_canonical_raw", failures)
    _check(DEFAULT_OUTPUT_ROOT in canonical_py, "Python recorder default output root is TELEOP/piper_canonical_raw", failures)

    forbidden = [
        "lerobot_robot_piper_quest3",
        "lerobot_teleoperator_quest3_vr",
        "LeRobotDataset",
        "lerobot-record",
        "legacy_piper_lerobot_recording_20260722",
    ]
    offenders: list[str] = []
    for path in _active_text_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{token}")
    _check(not offenders, "active code/tests/tools do not reference legacy LeRobot recording", failures)
    if offenders:
        for offender in offenders:
            print(f"  {offender}")

    layers = _read_text("docs/CAPTURE_SIDE_LAYERS.zh-CN.md")
    _check("操作员入口" in layers and "Source Of Truth" in layers, "capture layers document is present", failures)

    if failures:
        print()
        print("Cleanup gate failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print()
    print("Capture cleanup gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
