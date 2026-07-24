"""Preflight checks for the real bimanual Canonical Raw recorder."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CheckResult:
    name: str
    level: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[CheckResult, ...]

    @property
    def blocked(self) -> bool:
        return any(item.level == "BLOCKED" for item in self.checks)

    @property
    def warnings(self) -> int:
        return sum(item.level == "WARN" for item in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "blocked": self.blocked,
            "warnings": self.warnings,
            "checks": [item.to_dict() for item in self.checks],
        }


def _can_check(name: str) -> CheckResult:
    operstate = Path("/sys/class/net") / name / "operstate"
    if not operstate.exists():
        return CheckResult(f"CAN {name}", "BLOCKED", "interface does not exist")
    state = operstate.read_text(encoding="utf-8").strip()
    if state not in {"up", "unknown"}:
        return CheckResult(f"CAN {name}", "BLOCKED", f"interface state is {state}")
    detail = subprocess.run(
        ["ip", "-details", "link", "show", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if detail.returncode != 0:
        return CheckResult(f"CAN {name}", "BLOCKED", "cannot read CAN controller state")
    protocol_state = next(
        (
            candidate
            for candidate in ("ERROR-ACTIVE", "ERROR-WARNING", "ERROR-PASSIVE", "BUS-OFF", "STOPPED")
            if f"can state {candidate}" in detail.stdout
        ),
        "UNKNOWN",
    )
    if protocol_state != "ERROR-ACTIVE":
        return CheckResult(
            f"CAN {name}",
            "BLOCKED",
            f"controller is {protocol_state}; expected ERROR-ACTIVE",
        )
    return CheckResult(f"CAN {name}", "PASS", f"interface {state}, controller {protocol_state}")


def _disk_check(root: Path, minimum_free_gib: float) -> CheckResult:
    root.mkdir(parents=True, exist_ok=True)
    free_gib = shutil.disk_usage(root).free / (1024**3)
    if free_gib < minimum_free_gib:
        return CheckResult(
            "Recording disk",
            "BLOCKED",
            f"{free_gib:.1f} GiB free; requires at least {minimum_free_gib:.1f} GiB",
        )
    return CheckResult("Recording disk", "PASS", f"{free_gib:.1f} GiB free")


def _tls_check(files: Iterable[Path]) -> list[CheckResult]:
    return [
        CheckResult(f"TLS {path.name}", "PASS" if path.is_file() else "BLOCKED", str(path))
        for path in files
    ]


def _estop_check(*, required: bool) -> CheckResult:
    result = subprocess.run(
        ["pgrep", "-af", "run_estop_only.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        level = "BLOCKED" if required else "WARN"
        return CheckResult("Emergency-stop service", level, "service process not detected")
    ready_path = Path("/home/ylhp-e-ai/PIPER/local/emergency_stop_device/estop.ready")
    if not ready_path.is_file():
        level = "BLOCKED" if required else "WARN"
        return CheckResult(
            "Emergency-stop device",
            level,
            "service is running but the physical USB device is not ready",
        )
    return CheckResult("Emergency-stop device", "PASS", f"physical device ready: {ready_path}")


def run_static_preflight(
    *,
    output_root: Path,
    repo_root: Path,
    can_names: tuple[str, str],
    minimum_free_gib: float = 20.0,
    mock_hardware: bool = False,
    require_estop: bool = True,
) -> PreflightReport:
    checks: list[CheckResult] = [_disk_check(output_root, minimum_free_gib)]
    checks.extend(_tls_check((repo_root / "teleop/cert.pem", repo_root / "teleop/key.pem")))
    if mock_hardware:
        checks.append(CheckResult("Robot hardware", "WARN", "mock hardware enabled"))
    else:
        checks.extend(_can_check(name) for name in can_names)
        checks.append(_estop_check(required=require_estop))
    return PreflightReport(tuple(checks))
