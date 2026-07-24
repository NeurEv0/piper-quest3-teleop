"""Low-frequency host, control-loop, CAN, and GPU diagnostics."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


def _proc_cpu() -> tuple[int, int]:
    fields = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
    return sum(fields), idle


def _memory() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, raw = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            values[f"{key.lower()}_bytes"] = int(raw.split()[0]) * 1024
    return values


def _can_stats(name: str) -> dict[str, int | str]:
    root = Path("/sys/class/net") / name
    result: dict[str, int | str] = {"interface": name}
    try:
        result["operstate"] = (root / "operstate").read_text().strip()
        for key in ("rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped"):
            result[key] = int((root / "statistics" / key).read_text())
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)
    return result


def _network_stats() -> dict[str, dict[str, int | str]]:
    result: dict[str, dict[str, int | str]] = {}
    for root in Path("/sys/class/net").glob("*"):
        if root.name == "lo" or root.name.startswith("can"):
            continue
        values: dict[str, int | str] = {"operstate": "unknown"}
        try:
            values["operstate"] = (root / "operstate").read_text().strip()
            for key in ("rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped"):
                values[key] = int((root / "statistics" / key).read_text())
        except (OSError, ValueError) as exc:
            values["error"] = str(exc)
        result[root.name] = values
    return result


def _gpu_metrics() -> dict[str, object]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return {"status": "unavailable", "error": result.stderr.strip() or "no NVIDIA GPU data"}
        values = [value.strip() for value in result.stdout.splitlines()[0].split(",")]
        return {
            "status": "available",
            "utilization_percent": float(values[0]),
            "memory_used_mib": float(values[1]),
            "memory_total_mib": float(values[2]),
            "temperature_c": float(values[3]),
        }
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "error": str(exc)}


class SystemMetricsSampler:
    def __init__(self, output_root: Path, can_names: tuple[str, str] = ("can_left", "can_right")):
        self.output_root = Path(output_root)
        self.can_names = can_names
        self._previous_cpu = _proc_cpu()

    def sample(
        self,
        *,
        loop_duration_s: float,
        target_period_s: float,
        sources: dict[str, dict[str, object]],
        recorder_status: dict[str, Any],
    ) -> dict[str, object]:
        total, idle = _proc_cpu()
        previous_total, previous_idle = self._previous_cpu
        self._previous_cpu = (total, idle)
        delta_total = max(1, total - previous_total)
        cpu_percent = 100.0 * (1.0 - (idle - previous_idle) / delta_total)
        disk = shutil.disk_usage(self.output_root)
        source_issues = [
            {"source": name, "level": status.get("level"), "message": status.get("message")}
            for name, status in sources.items()
            if status.get("level") in {"WARN", "BLOCKED"}
        ]
        return {
            "host_monotonic_ns": time.monotonic_ns(),
            "host_wall_time_ns": time.time_ns(),
            "cpu": {"utilization_percent": cpu_percent, "load_average": list(os.getloadavg())},
            "memory": _memory(),
            "gpu": _gpu_metrics(),
            "disk": {"total_bytes": disk.total, "used_bytes": disk.used, "free_bytes": disk.free},
            "control_loop": {
                "target_hz": 1.0 / target_period_s,
                "actual_hz": 1.0 / max(loop_duration_s, 1e-9),
                "duration_ms": loop_duration_s * 1000.0,
                "overrun": loop_duration_s > target_period_s,
            },
            "can": {"left": _can_stats(self.can_names[0]), "right": _can_stats(self.can_names[1])},
            "network": {
                "interfaces": _network_stats(),
                "quest3_event_age_s": sources.get("quest3", {}).get("event_age_s"),
                "latency_note": "event age is freshness, not round-trip latency",
            },
            "active_issues": source_issues,
            "sources": sources,
            "recorder": recorder_status,
        }
