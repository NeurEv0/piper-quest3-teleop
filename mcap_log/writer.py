"""Low-level per-Episode MCAP writer with failure isolation."""

from __future__ import annotations

import json
import os
import struct
import time
from pathlib import Path
from typing import Any

import numpy as np

from .contract import (
    CALIBRATION_TOPIC,
    CAMERA_TOPIC_TEMPLATE,
    CAPABILITIES_TOPIC,
    EPISODE_METADATA_TOPIC,
    IMAGE_HEADER_SCHEMA,
    IMAGE_SCHEMA_NAME,
    JSON_SCHEMA,
    JSON_SCHEMA_NAME,
    LIBRARY,
    LANGUAGE_INSTRUCTION_TOPIC,
    PROFILE,
    ROW_TOPICS,
    TF_TOPIC,
    TF_STATUS_TOPIC,
    default_capabilities,
)


_IMAGE_PREFIX = struct.Struct("<4sHI")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def encode_image_envelope(header: dict[str, Any], jpeg: bytes) -> bytes:
    header_bytes = _json_bytes(header)
    return _IMAGE_PREFIX.pack(b"PIMG", 1, len(header_bytes)) + header_bytes + jpeg


def decode_image_envelope(data: bytes) -> tuple[dict[str, Any], bytes]:
    if len(data) < _IMAGE_PREFIX.size:
        raise ValueError("image payload is shorter than the PIMG prefix")
    magic, version, header_size = _IMAGE_PREFIX.unpack_from(data)
    if magic != b"PIMG" or version != 1:
        raise ValueError("unsupported image envelope")
    header_start = _IMAGE_PREFIX.size
    header_end = header_start + header_size
    if header_end > len(data):
        raise ValueError("image header extends beyond payload")
    return json.loads(data[header_start:header_end]), data[header_end:]


class McapLogWriter:
    """Write one crash-identifiable MCAP file inside a Canonical episode."""

    def __init__(
        self,
        work_dir: Path,
        *,
        episode_metadata: dict[str, Any],
        calibration_snapshot: dict[str, Any] | None,
        jpeg_quality: int = 90,
    ):
        from mcap.writer import CompressionType, Writer

        self.inprogress_path = Path(work_dir) / "raw.mcap.inprogress"
        self.final_path = Path(work_dir) / "raw.mcap"
        self._stream = self.inprogress_path.open("wb")
        self._writer = Writer(
            self._stream,
            chunk_size=4 * 1024 * 1024,
            compression=CompressionType.ZSTD,
            enable_crcs=True,
            enable_data_crcs=True,
        )
        self._writer.start(profile=PROFILE, library=LIBRARY)
        self._json_schema_id = self._writer.register_schema(JSON_SCHEMA_NAME, "jsonschema", JSON_SCHEMA)
        self._image_schema_id = self._writer.register_schema(
            IMAGE_SCHEMA_NAME, "piper.image-envelope.v1", IMAGE_HEADER_SCHEMA
        )
        self._channels: dict[tuple[str, str], int] = {}
        self._sequences: dict[str, int] = {}
        self._jpeg_quality = max(50, min(100, int(jpeg_quality)))
        self.message_counts: dict[str, int] = {}
        self._closed = False

        capabilities = default_capabilities(str(episode_metadata.get("camera_mode", "mosaic")))
        calibration = calibration_snapshot or {
            "calibration_version": "missing",
            "status": "not_configured",
            "transforms": [],
        }
        self._camera_frame_ids = dict(calibration.get("frame_ids", {}))
        tf_status = str(calibration.get("tf_status", calibration.get("status", "missing")))
        tf_available = tf_status in {"valid", "usable_with_limitations"} and bool(calibration.get("transforms"))
        now_wall_ns = time.time_ns()
        now_mono_ns = time.monotonic_ns()
        self._writer.add_metadata(
            "piper.log",
            {
                "profile": PROFILE,
                "episode_id": str(episode_metadata.get("episode_id", "unknown")),
                "session_id": str(episode_metadata.get("session_id", "unknown")),
                "created_host_wall_time_ns": str(now_wall_ns),
                "created_host_monotonic_ns": str(now_mono_ns),
            },
        )
        self._writer.add_attachment(
            create_time=now_wall_ns,
            log_time=now_wall_ns,
            name="capabilities.json",
            media_type="application/json",
            data=_json_bytes(capabilities),
        )
        self._writer.add_attachment(
            create_time=now_wall_ns,
            log_time=now_wall_ns,
            name="calibration_snapshot.json",
            media_type="application/json",
            data=_json_bytes(calibration),
        )
        self.write_json(EPISODE_METADATA_TOPIC, episode_metadata)
        if episode_metadata.get("language_instruction"):
            self.write_json(
                LANGUAGE_INSTRUCTION_TOPIC,
                {
                    "annotation_schema": episode_metadata.get("annotation_schema", "piper.vla.language.v1"),
                    "language_instruction": episode_metadata["language_instruction"],
                    "task_id": episode_metadata.get("task_id", "unknown"),
                },
            )
        self.write_json(CAPABILITIES_TOPIC, capabilities)
        self.write_json(CALIBRATION_TOPIC, calibration)
        self.write_json(
            TF_STATUS_TOPIC,
            {
                "status": "available" if tf_available else "not_available",
                "confidence": "mixed" if tf_status == "usable_with_limitations" else tf_status,
                "calibration_version": calibration.get("calibration_version", "unknown"),
            },
        )
        if tf_available:
            self.write_json(
                TF_TOPIC,
                {
                    "calibration_version": calibration.get("calibration_version", "unknown"),
                    "transforms": calibration["transforms"],
                },
            )

    def _channel(self, topic: str, encoding: str, schema_id: int, metadata: dict[str, str] | None = None) -> int:
        key = (topic, encoding)
        channel_id = self._channels.get(key)
        if channel_id is None:
            channel_id = self._writer.register_channel(
                topic=topic,
                message_encoding=encoding,
                schema_id=schema_id,
                metadata=metadata or {},
            )
            self._channels[key] = channel_id
        return channel_id

    def _sequence(self, topic: str) -> int:
        sequence = self._sequences.get(topic, 0)
        self._sequences[topic] = sequence + 1
        return sequence

    def write_json(self, topic: str, message: dict[str, Any]) -> None:
        row = dict(message)
        sequence = self._sequence(topic)
        row.setdefault("host_monotonic_ns", time.monotonic_ns())
        row.setdefault("host_wall_time_ns", time.time_ns())
        row.setdefault("source_timestamp_ns", row["host_monotonic_ns"])
        row["sequence_id"] = sequence
        channel_id = self._channel(topic, "json", self._json_schema_id)
        self._writer.add_message(
            channel_id=channel_id,
            log_time=int(row["host_wall_time_ns"]),
            publish_time=int(row["host_wall_time_ns"]),
            sequence=sequence,
            data=_json_bytes(row),
        )
        self.message_counts[topic] = self.message_counts.get(topic, 0) + 1

    def write_row(self, kind: str, row: dict[str, Any]) -> None:
        topic = ROW_TOPICS.get(kind)
        if topic is not None:
            self.write_json(topic, row)

    def write_camera(
        self,
        camera_name: str,
        frame: np.ndarray,
        *,
        lifecycle: dict[str, Any],
    ) -> None:
        import cv2

        image = np.asarray(frame, dtype=np.uint8)
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError(f"unexpected RGB frame shape {image.shape}")
        topic = CAMERA_TOPIC_TEMPLATE.format(camera_name=camera_name)
        sequence = self._sequence(topic)
        ok, encoded = cv2.imencode(
            ".jpg",
            cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality],
        )
        if not ok:
            raise RuntimeError(f"JPEG encoding failed for {camera_name}")
        height, width = image.shape[:2]
        header = {
            **{key: value for key, value in lifecycle.items() if key != "frame"},
            "camera_name": camera_name,
            "frame_id": self._camera_frame_ids.get(camera_name, f"camera_{camera_name}_optical_frame"),
            "host_monotonic_ns": int(lifecycle["host_monotonic_ns"]),
            "host_wall_time_ns": int(lifecycle["host_wall_time_ns"]),
            "source_timestamp_ns": int(lifecycle["source_timestamp_ns"]),
            "sequence_id": sequence,
            "width": int(width),
            "height": int(height),
            "pixel_encoding": "rgb8",
            "compression": "jpeg",
            "jpeg_quality": self._jpeg_quality,
        }
        channel_id = self._channel(
            topic,
            "piper.jpeg.v1",
            self._image_schema_id,
            {"camera_name": camera_name, "frame_id": header["frame_id"]},
        )
        self._writer.add_message(
            channel_id=channel_id,
            log_time=int(lifecycle["host_wall_time_ns"]),
            publish_time=int(lifecycle["host_wall_time_ns"]),
            sequence=sequence,
            data=encode_image_envelope(header, bytes(encoded)),
        )
        self.message_counts[topic] = self.message_counts.get(topic, 0) + 1

    def finish(self, result: dict[str, Any], *, aborted: bool = False) -> Path:
        if self._closed:
            return self.final_path
        now_ns = time.time_ns()
        self.write_json(
            "/episode/event",
            {
                "event": "recording_aborted" if aborted else "recording_finished",
                "result": result,
            },
        )
        self._writer.add_metadata(
            "piper.episode.result",
            {
                "recording_state": "aborted" if aborted else "finalized",
                "task_success": str(bool(result.get("task_success", False))).lower(),
                "failure_reason": str(result.get("failure_reason", "unknown")),
                "finished_host_wall_time_ns": str(now_ns),
                "message_counts_json": json.dumps(self.message_counts, sort_keys=True),
            },
        )
        self._writer.finish()
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()
        os.replace(self.inprogress_path, self.final_path)
        self._closed = True
        return self.final_path

    def abandon(self) -> None:
        """Close an incomplete sidecar without affecting Canonical Raw finalization."""
        if self._closed:
            return
        try:
            self._stream.close()
        finally:
            self._closed = True
