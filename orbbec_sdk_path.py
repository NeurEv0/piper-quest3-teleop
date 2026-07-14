"""Workspace-local resolver for the Orbbec SDK shared library.

Independence goal
-----------------
LeRobot's ``OrbbecCameraConfig.sdk_lib_path`` defaults to a path *inside* the
``piper_lerobot-main`` fork tree. Pointing the workspace's camera configs at
that default makes this workspace depend on the fork's on-disk layout for a
runtime binary. To keep the workspace self-contained, we vendor the Orbbec SDK
under ``third_party/orbbec_sdk/lib`` (the SDK is built with ``RPATH=$ORIGIN``,
so the directory is relocatable) and resolve the library path here.

Resolution order (first hit wins):

1. ``$PIPER_ORBBEC_SDK_LIB`` — explicit override (a file or a dir containing
   ``libOrbbecSDK.so``). Lets ops point at a system install without code edits.
2. Workspace-vendored copy: ``<repo_root>/third_party/orbbec_sdk/lib``.
3. Fork fallback: the original ``piper_lerobot-main`` install path, so nothing
   breaks if the vendored copy was removed.

The returned value is always an absolute path to the ``.so`` (the vendored
``libOrbbecSDK.so`` is a symlink chain to ``libOrbbecSDK.so.1.10.35``; ctypes
follows it fine).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent
_LIB_NAME = "libOrbbecSDK.so"

# Workspace-vendored SDK (preferred — keeps this workspace independent).
_VENDORED_LIB = _REPO_ROOT / "third_party" / "orbbec_sdk" / "lib" / _LIB_NAME

# Last-resort fallback: the fork's install tree (original hardcoded default).
_FORK_FALLBACK_LIB = Path(
    "/home/ylhp-e-ai/piper_lerobot-main/src/OrbbecSDK_ROS2-main/"
    "install/orbbec_camera/lib/libOrbbecSDK.so"
)


def _as_lib_file(candidate: Path) -> Path | None:
    """Normalize a candidate (file or containing dir) to an existing .so path."""
    if candidate.is_dir():
        candidate = candidate / _LIB_NAME
    return candidate if candidate.exists() else None


def resolve_orbbec_sdk_lib_path() -> str:
    """Return an absolute path to ``libOrbbecSDK.so`` for OrbbecCameraConfig.

    Never raises — falls back to the vendored path string even if nothing is
    found, so the error surfaces later at ``camera.connect()`` with a clear
    FileNotFoundError from LeRobot's own SDK loader.
    """
    env = os.environ.get("PIPER_ORBBEC_SDK_LIB")
    if env:
        hit = _as_lib_file(Path(env).expanduser())
        if hit is not None:
            return str(hit.resolve())
        logger.warning(
            "PIPER_ORBBEC_SDK_LIB=%s does not point at %s; ignoring.", env, _LIB_NAME
        )

    if _VENDORED_LIB.exists():
        return str(_VENDORED_LIB.resolve())

    if _FORK_FALLBACK_LIB.exists():
        logger.warning(
            "Vendored Orbbec SDK not found at %s; falling back to fork tree %s. "
            "Run scripts/setup_orbbec_sdk.sh to restore workspace independence.",
            _VENDORED_LIB,
            _FORK_FALLBACK_LIB,
        )
        return str(_FORK_FALLBACK_LIB.resolve())

    # Nothing found — return the vendored path so the eventual error names the
    # location the user is expected to populate.
    return str(_VENDORED_LIB)


# Convenience module-level constant (resolved once at import).
ORBBEC_SDK_LIB_PATH: str = resolve_orbbec_sdk_lib_path()
