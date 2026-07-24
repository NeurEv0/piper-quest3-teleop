"""Versioned English language annotation contract for VLA training exports."""

from __future__ import annotations

import re
from typing import Any


ANNOTATION_SCHEMA = "piper.vla.language.v1"
PRIMITIVES = (
    "approach",
    "grasp",
    "lift",
    "transport",
    "place",
    "release",
    "push",
    "pull",
    "rotate",
    "align",
    "hold",
    "retract",
    "reset",
)
ARMS = ("left", "right", "bimanual")
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def normalize_english_text(value: object, field: str, *, max_length: int = 300) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) < 3:
        raise ValueError(f"{field} must contain an English instruction")
    if len(text) > max_length:
        raise ValueError(f"{field} exceeds {max_length} characters")
    if _CJK.search(text):
        raise ValueError(f"{field} must be written in English")
    if not any(character.isalpha() for character in text):
        raise ValueError(f"{field} must contain English words")
    return text


def normalize_language_action(payload: dict[str, Any]) -> dict[str, str]:
    primitive = str(payload.get("primitive", "")).strip().lower()
    arm = str(payload.get("arm", "")).strip().lower()
    if primitive not in PRIMITIVES:
        raise ValueError(f"primitive must be one of: {', '.join(PRIMITIVES)}")
    if arm not in ARMS:
        raise ValueError(f"arm must be one of: {', '.join(ARMS)}")
    return {
        "annotation_schema": ANNOTATION_SCHEMA,
        "primitive": primitive,
        "arm": arm,
        "language_action": normalize_english_text(payload.get("language_action"), "language_action"),
        "object": normalize_english_text(payload.get("object", "unspecified object"), "object", max_length=100),
        "target": normalize_english_text(payload.get("target", "unspecified target"), "target", max_length=100),
        "source": "operator_dashboard",
    }
