#!/usr/bin/env python3
"""Create an auditable diagnostic camera-resynced Canonical Raw session.

This tool is intentionally offline-only. It derives a new session from an
existing sample by dropping camera frames that cannot be grouped into a complete
multi-camera set within the C2 sync threshold. It does not prove the real
capture-side fix; it only creates a diagnostic fixture for downstream closure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import cv2
    import pyarrow as pa
    import pyarrow.parquet as pq
except Exception as exc:  # pragma: no cover - exercised by CLI environment
    raise SystemExit(f"missing runtime dependency: {exc}") from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from canonical_raw.contract import C2_VALIDATION_THRESHOLDS  # noqa: E402
from canonical_raw.validator import validate_episode  # noqa: E402


CAMERA_PREFIX = "camera_"
CAMERA_TIMESTAMPS = "camera_timestamps"
DEFAULT_CAMERA_NAMES = ("cam_front", "cam_left_wrist", "cam_right_wrist")
DERIVATION_NOTE = (
    "diagnostic derived/resynced session only; does not replace true hardware "
    "recapture evidence"
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_default(value: Any) -> Any:
    if hasattr(value, "as_py"):
        return value.as_py()
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, separators=(",", ":"), default=_json_default) + "\n")


def _count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


def _parquet_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return pq.read_table(path).num_rows


def _copy_non_camera_files(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    skip_names = {
        "metadata.json",
        "manifest.json",
        "validation.json",
        "mcap_validation.json",
        "raw.mcap",
        f"{CAMERA_TIMESTAMPS}.parquet",
        f"{CAMERA_TIMESTAMPS}.jsonl",
    }
    for item in sorted(source.iterdir()):
        if not item.is_file():
            continue
        if item.name in skip_names:
            continue
        if item.name.startswith(CAMERA_PREFIX) and item.suffix == ".mp4":
            continue
        shutil.copy2(item, destination / item.name)


def _camera_names(rows: list[dict[str, Any]], requested: tuple[str, ...] | None) -> tuple[str, ...]:
    present = tuple(sorted({str(row["camera_name"]) for row in rows if row.get("camera_name")}))
    if requested:
        missing = sorted(set(requested) - set(present))
        if missing:
            raise ValueError(f"requested camera streams are missing: {missing}")
        return requested
    if set(DEFAULT_CAMERA_NAMES) <= set(present):
        return DEFAULT_CAMERA_NAMES
    if len(present) < 3:
        raise ValueError(f"expected at least 3 camera streams, found {present}")
    return present[:3]


def _group_camera_rows(
    rows: list[dict[str, Any]],
    camera_names: tuple[str, ...],
    threshold_ns: int,
) -> tuple[list[dict[str, dict[str, Any]]], dict[str, Any]]:
    by_camera: dict[str, list[dict[str, Any]]] = {name: [] for name in camera_names}
    for row in rows:
        name = str(row.get("camera_name"))
        if name in by_camera:
            by_camera[name].append(dict(row))
    for name in camera_names:
        by_camera[name].sort(key=lambda item: int(item["host_monotonic_ns"]))
        if not by_camera[name]:
            raise ValueError(f"camera stream has no rows: {name}")

    original_counts = {name: len(by_camera[name]) for name in camera_names}
    cursors = {name: 0 for name in camera_names}
    stale_drops = defaultdict(int)
    groups: list[dict[str, dict[str, Any]]] = []
    skews_ms: list[float] = []

    while all(cursors[name] < len(by_camera[name]) for name in camera_names):
        heads = {name: by_camera[name][cursors[name]] for name in camera_names}
        stamps = {name: int(row["host_monotonic_ns"]) for name, row in heads.items()}
        min_name = min(stamps, key=stamps.get)
        skew_ns = max(stamps.values()) - min(stamps.values())
        if skew_ns <= threshold_ns:
            groups.append(heads)
            skews_ms.append(round(skew_ns / 1e6, 6))
            for name in camera_names:
                cursors[name] += 1
        else:
            stale_drops[min_name] += 1
            cursors[min_name] += 1

    tail_drops = {
        name: len(by_camera[name]) - cursors[name]
        for name in camera_names
        if len(by_camera[name]) > cursors[name]
    }
    kept_counts = {name: len(groups) for name in camera_names}
    dropped_counts = {
        name: original_counts[name] - kept_counts[name]
        for name in camera_names
    }
    stats = {
        "camera_names": list(camera_names),
        "threshold_ms": round(threshold_ns / 1e6, 6),
        "original_counts": original_counts,
        "kept_counts": kept_counts,
        "dropped_counts": dropped_counts,
        "stale_alignment_drops": dict(sorted(stale_drops.items())),
        "tail_incomplete_drops": dict(sorted(tail_drops.items())),
        "total_groups": len(groups),
        "total_rows": len(groups) * len(camera_names),
        "max_group_skew_ms": max(skews_ms) if skews_ms else None,
        "p95_group_skew_ms": _percentile(skews_ms, 0.95),
    }
    return groups, stats


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * fraction
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return round(ordered[lower], 6)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 6)


def _build_resynced_rows(groups: list[dict[str, dict[str, Any]]], camera_names: tuple[str, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    row_sequence = 0
    for group_index, group in enumerate(groups):
        for name in camera_names:
            row = dict(group[name])
            row["camera_name"] = name
            row["camera_stream_sequence_id"] = group_index
            row["video_frame_index"] = group_index
            row["row_sequence_id"] = row_sequence
            row["decoded"] = bool(row.get("decoded", True))
            rows.append(row)
            row_sequence += 1
    return rows


def _rewrite_video(source: Path, destination: Path, selected_indices: list[int]) -> dict[str, Any]:
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"failed to open source video: {source}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if width <= 0 or height <= 0:
        capture.release()
        raise RuntimeError(f"invalid source video dimensions: {source}")

    writer = cv2.VideoWriter(
        str(destination),
        cv2.VideoWriter_fourcc(*"mp4v"),
        float(fps),
        (width, height),
    )
    if not writer.isOpened():
        capture.release()
        raise RuntimeError(f"failed to open derived video writer: {destination}")

    selected = set(int(index) for index in selected_indices)
    max_selected = max(selected) if selected else -1
    source_index = 0
    written = 0
    while source_index <= max_selected:
        ok, frame = capture.read()
        if not ok:
            break
        if source_index in selected:
            writer.write(frame)
            written += 1
        source_index += 1

    capture.release()
    writer.release()
    if written != len(selected_indices):
        raise RuntimeError(
            f"video rewrite count mismatch for {source.name}: wrote {written}, expected {len(selected_indices)}"
        )
    return {"fps": float(fps), "width": width, "height": height, "written_frames": written}


def _rewrite_videos(
    source_episode: Path,
    destination_episode: Path,
    groups: list[dict[str, dict[str, Any]]],
    camera_names: tuple[str, ...],
) -> dict[str, Any]:
    video_stats: dict[str, Any] = {}
    for name in camera_names:
        selected_indices = [int(group[name]["video_frame_index"]) for group in groups]
        source = source_episode / f"camera_{name}.mp4"
        destination = destination_episode / f"camera_{name}.mp4"
        video_stats[name] = _rewrite_video(source, destination, selected_indices)
    return video_stats


def _camera_sync_metadata(rows: list[dict[str, Any]], camera_names: tuple[str, ...]) -> dict[str, Any]:
    counts = {name: 0 for name in camera_names}
    first: dict[str, int] = {}
    last: dict[str, int] = {}
    by_group: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        name = str(row["camera_name"])
        stamp = int(row["host_monotonic_ns"])
        counts[name] += 1
        first.setdefault(name, stamp)
        last[name] = stamp
        by_group[int(row["camera_stream_sequence_id"])].append(stamp)
    skews = [
        (max(stamps) - min(stamps)) / 1e6
        for stamps in by_group.values()
        if len(stamps) == len(camera_names)
    ]
    return {
        "camera_frame_counts": dict(sorted(counts.items())),
        "max_frame_count_skew": max(counts.values()) - min(counts.values()) if counts else 0,
        "first_frame_skew_ms": round((max(first.values()) - min(first.values())) / 1e6, 6) if first else 0.0,
        "last_frame_skew_ms": round((max(last.values()) - min(last.values())) / 1e6, 6) if last else 0.0,
        "max_group_skew_ms": round(max(skews), 6) if skews else None,
        "p95_group_skew_ms": _percentile(skews, 0.95),
    }


def _stream_counts(episode: Path, camera_rows: int) -> dict[str, int]:
    return {
        "control": _parquet_count(episode / "control.parquet"),
        "robot_feedback": _parquet_count(episode / "robot_feedback.parquet"),
        "vr_input": _parquet_count(episode / "vr_input.parquet"),
        "camera": camera_rows,
        "event": _count_jsonl(episode / "events.jsonl"),
        "language_action": _count_jsonl(episode / "language_actions.jsonl"),
    }


def _write_manifest(
    source_episode: Path,
    destination_episode: Path,
    metadata: dict[str, Any],
    stream_counts: dict[str, int],
    camera_sync: dict[str, Any],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    source_manifest_path = source_episode / "manifest.json"
    manifest = _read_json(source_manifest_path) if source_manifest_path.is_file() else {}
    files = []
    for item in sorted(destination_episode.iterdir()):
        if item.is_file() and item.name != "manifest.json":
            files.append({"path": item.name, "bytes": item.stat().st_size, "sha256": _sha256(item)})
    manifest.update(
        {
            "session_id": metadata.get("session_id"),
            "episode_id": metadata.get("episode_id"),
            "schema_version": manifest.get("schema_version", "piper_canonical_raw_manifest_v1"),
            "files": files,
            "stream_counts": stream_counts,
            "stream_sequence_counts": dict(stream_counts),
            "camera_sync": camera_sync,
            "diagnostic_derived": True,
            "resync_provenance": provenance,
        }
    )
    _write_json(destination_episode / "manifest.json", manifest)
    return manifest


def derive_episode(
    source_episode: Path,
    destination_episode: Path,
    *,
    source_session: Path,
    threshold_ms: float,
    requested_cameras: tuple[str, ...] | None,
) -> dict[str, Any]:
    _copy_non_camera_files(source_episode, destination_episode)
    camera_rows = pq.read_table(source_episode / f"{CAMERA_TIMESTAMPS}.parquet").to_pylist()
    camera_names = _camera_names(camera_rows, requested_cameras)
    groups, resync_stats = _group_camera_rows(camera_rows, camera_names, int(threshold_ms * 1_000_000))
    if not groups:
        raise RuntimeError(f"no synchronized camera groups could be derived for {source_episode.name}")

    resynced_rows = _build_resynced_rows(groups, camera_names)
    table = pa.Table.from_pylist(resynced_rows)
    pq.write_table(table, destination_episode / f"{CAMERA_TIMESTAMPS}.parquet", compression="zstd")
    _write_jsonl(destination_episode / f"{CAMERA_TIMESTAMPS}.jsonl", resynced_rows)
    video_stats = _rewrite_videos(source_episode, destination_episode, groups, camera_names)

    metadata = _read_json(source_episode / "metadata.json")
    source_metadata = dict(metadata)
    camera_sync = _camera_sync_metadata(resynced_rows, camera_names)
    stream_counts = _stream_counts(destination_episode, len(resynced_rows))
    provenance = {
        "diagnostic_derived": True,
        "source_session_path": str(source_session.resolve()),
        "source_episode_path": str(source_episode.resolve()),
        "derived_at_unix_ns": time.time_ns(),
        "method": (
            "offline greedy timestamp resync: sort each camera stream by host_monotonic_ns, "
            "emit only complete three-camera groups with max-min host timestamp <= threshold, "
            "drop stale and incomplete-tail camera frames, rewrite MP4s and camera_timestamps"
        ),
        "threshold_ms": threshold_ms,
        "limitations": DERIVATION_NOTE,
        "resync_stats": resync_stats,
        "video_stats": video_stats,
    }
    metadata.update(
        {
            "diagnostic_derived": True,
            "derived_from_session_path": str(source_session.resolve()),
            "derived_from_episode_path": str(source_episode.resolve()),
            "derivation_note": DERIVATION_NOTE,
            "resync_provenance": provenance,
            "camera_sync": camera_sync,
            "stream_counts": stream_counts,
            "stream_sequence_counts": dict(stream_counts),
            "dropped_camera_frames": sum(resync_stats["dropped_counts"].values()),
        }
    )
    if "mcap_log" in metadata:
        metadata["mcap_log"] = {
            "status": "omitted",
            "reason": "diagnostic_derived_resync_omits_original_raw_mcap",
            "source_status": source_metadata.get("mcap_log"),
        }
    _write_json(destination_episode / "metadata.json", metadata)
    _write_json(destination_episode / "resync_provenance.json", provenance)

    manifest = _write_manifest(source_episode, destination_episode, metadata, stream_counts, camera_sync, provenance)
    report = validate_episode(destination_episode)
    _write_json(destination_episode / "validation.json", report.to_dict())
    if not report.valid:
        manifest["validation_status"] = "fail"
        _write_json(destination_episode / "manifest.json", manifest)
    return {
        "episode_id": destination_episode.name,
        "source_episode": str(source_episode),
        "derived_episode": str(destination_episode),
        "valid": report.valid,
        "errors": list(report.errors),
        "warnings": list(report.warnings),
        "reason_codes": list(report.reason_codes),
        "metrics": {
            "camera_frame_count_skew": report.metrics.get("camera_frame_count_skew"),
            "camera_multicamera_sync_ms": report.metrics.get("camera.multicamera_sync_ms"),
        },
        "resync_stats": resync_stats,
        "video_stats": video_stats,
    }


def derive_session(source: Path, destination: Path, *, threshold_ms: float, requested_cameras: tuple[str, ...] | None) -> dict[str, Any]:
    if source.resolve() == destination.resolve():
        raise ValueError("destination must not be the source session")
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    if "diagnostic" not in destination.name or ("derived" not in str(destination) and "resynced" not in destination.name):
        raise ValueError("destination path must clearly include diagnostic and derived/resynced")

    destination.mkdir(parents=True)
    session_metadata = _read_json(source / "session.json") if (source / "session.json").is_file() else {}
    session_metadata.update(
        {
            "diagnostic_derived": True,
            "derived_from_session_path": str(source.resolve()),
            "derivation_note": DERIVATION_NOTE,
            "resync_method": "offline greedy timestamp grouping with MP4 rewrite",
            "resync_threshold_ms": threshold_ms,
        }
    )
    _write_json(destination / "session.json", session_metadata)
    if (source / "session_events.jsonl").is_file():
        shutil.copy2(source / "session_events.jsonl", destination / "session_events.jsonl")

    episode_summaries = []
    for source_episode in sorted(path for path in source.iterdir() if path.is_dir() and path.name.startswith("episode_")):
        episode_summaries.append(
            derive_episode(
                source_episode,
                destination / source_episode.name,
                source_session=source,
                threshold_ms=threshold_ms,
                requested_cameras=requested_cameras,
            )
        )

    valid_count = sum(1 for episode in episode_summaries if episode["valid"])
    summary = {
        "diagnostic_derived": True,
        "source_session_path": str(source.resolve()),
        "derived_session_path": str(destination.resolve()),
        "derivation_note": DERIVATION_NOTE,
        "threshold_ms": threshold_ms,
        "episode_count": len(episode_summaries),
        "valid_episode_count": valid_count,
        "invalid_episode_count": len(episode_summaries) - valid_count,
        "episodes": episode_summaries,
    }
    _write_json(destination / "resync_summary.json", summary)
    _write_summary_markdown(destination / "RESYNC_DIAGNOSTIC_SUMMARY.md", summary)
    return summary


def _write_summary_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Diagnostic Resynced Session",
        "",
        f"- Source: `{summary['source_session_path']}`",
        f"- Derived: `{summary['derived_session_path']}`",
        f"- Threshold: `{summary['threshold_ms']} ms`",
        f"- Valid episodes: `{summary['valid_episode_count']}/{summary['episode_count']}`",
        f"- Limitation: {DERIVATION_NOTE}",
        "",
        "| episode | valid | kept groups | dropped frames | max skew ms | errors |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for episode in summary["episodes"]:
        stats = episode["resync_stats"]
        dropped = sum(stats["dropped_counts"].values())
        errors = ", ".join(episode["reason_codes"]) if episode["reason_codes"] else "-"
        lines.append(
            f"| `{episode['episode_id']}` | {episode['valid']} | {stats['total_groups']} | "
            f"{dropped} | {stats['max_group_skew_ms']} | {errors} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="source canonical raw session")
    parser.add_argument("--output", type=Path, required=True, help="new diagnostic derived session directory")
    parser.add_argument(
        "--threshold-ms",
        type=float,
        default=float(C2_VALIDATION_THRESHOLDS["multicamera_sync_ms_max"]),
        help="maximum max-min host timestamp skew per camera group",
    )
    parser.add_argument(
        "--camera",
        action="append",
        dest="cameras",
        help="camera name to require; repeat three times. Defaults to standard Piper cameras.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    requested = tuple(args.cameras) if args.cameras else None
    summary = derive_session(args.source, args.output, threshold_ms=args.threshold_ms, requested_cameras=requested)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["invalid_episode_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
