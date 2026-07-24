"""Preflight checks for the real bimanual Canonical Raw recorder."""

from __future__ import annotations

import shutil
import socket
import subprocess
import importlib
import os
import re
from dataclasses import replace
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CheckResult:
    name: str
    level: str
    message: str
    code: str = "unspecified"

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


def _tcp_port_check(port: int) -> CheckResult:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", int(port)))
    except OSError as exc:
        return CheckResult(
            f"TCP port {port}",
            "BLOCKED",
            f"already in use ({exc.strerror or exc})",
        )
    finally:
        sock.close()
    return CheckResult(f"TCP port {port}", "PASS", "available")


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
    tcp_ports: tuple[int, ...] = (),
) -> PreflightReport:
    checks: list[CheckResult] = [_disk_check(output_root, minimum_free_gib)]
    checks.extend(_tls_check((repo_root / "teleop/cert.pem", repo_root / "teleop/key.pem")))
    checks.extend(_tcp_port_check(port) for port in tcp_ports)
    if mock_hardware:
        checks.append(CheckResult("Robot hardware", "WARN", "mock hardware enabled"))
    else:
        checks.extend(_can_check(name) for name in can_names)
        checks.append(_estop_check(required=require_estop))
    return PreflightReport(tuple(checks))


def validate_cleaning_ready(
    *,
    output_root: Path,
    capture_contract_version: str,
    expected_contract_version: str,
    control_rate_hz: float,
    camera_mode: str,
    allow_no_cameras: bool,
    calibration_status: str | None,
    metadata: dict[str, object],
    base_report: PreflightReport | None = None,
) -> PreflightReport:
    """Compute the single launcher/Dashboard cleaning-ready decision."""
    checks = [
        replace(item, code=f"preflight.{re.sub('[^a-z0-9]+', '_', item.name.lower()).strip('_')}")
        if item.code == "unspecified" else item
        for item in (base_report.checks if base_report else ())
    ]
    for module, code in (("pyarrow", "dependency.pyarrow_missing"), ("cv2", "dependency.opencv_missing")):
        try:
            importlib.import_module(module)
            available, detail = True, "imported"
        except Exception as exc:
            available, detail = False, f"import failed: {type(exc).__name__}"
        checks.append(CheckResult(module, "PASS" if available else "BLOCKED", detail, code))
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        writable = output_root.is_dir() and os.access(output_root, os.W_OK)
    except OSError:
        writable = False
    checks.append(CheckResult("Output directory", "PASS" if writable else "BLOCKED", str(output_root), "output.not_writable"))
    contract_ok = capture_contract_version == expected_contract_version
    checks.append(CheckResult("Capture contract", "PASS" if contract_ok else "BLOCKED", capture_contract_version, "contract.version_mismatch"))
    rate_ok = control_rate_hz > 0 and float(control_rate_hz).is_integer()
    checks.append(CheckResult("Fixed control rate", "PASS" if rate_ok else "BLOCKED", f"{control_rate_hz:g} Hz", "timing.invalid_fixed_rate"))
    camera_ok = camera_mode == "mosaic" or (allow_no_cameras and camera_mode == "off")
    checks.append(CheckResult("Camera mode contract", "PASS" if camera_ok else "BLOCKED", camera_mode, "camera.mode_not_cleaning_ready"))
    calibration_ok = calibration_status in {"valid", "calibrated", "verified", "usable"}
    calibration_limited = calibration_status == "usable_with_limitations"
    calibration_level = "PASS" if calibration_ok else "WARN" if calibration_limited else "BLOCKED"
    calibration_code = "calibration.limited" if calibration_limited else "calibration.not_valid"
    checks.append(CheckResult("Calibration", calibration_level, str(calibration_status or "missing"), calibration_code))
    for key in ("operator_id", "task_id", "language_instruction", "robot_id", "scene_id"):
        present = bool(metadata.get(key))
        checks.append(CheckResult(f"Metadata {key}", "PASS" if present else "BLOCKED", "present" if present else "missing", f"metadata.{key}_missing"))
    return PreflightReport(tuple(checks))
