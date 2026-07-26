"""LeRobot-independent Canonical Raw recording primitives."""

from .contract import ACTION_SEMANTICS_VERSION, CAPTURE_CONTRACT_VERSION, SESSION_EVENT_SCHEMA_VERSION
from .preflight import CheckResult, PreflightReport, run_static_preflight, validate_cleaning_ready
from .fixtures import write_c1_session_fixtures, write_cleaning_ready_fixture
from .recovery import inspect_inprogress, write_diagnostic_report
from .session_events import reconstruct_episode_boundaries
from .recorder import AsyncCanonicalRecorder, RecorderState
from .validator import ValidationReport, validate_episode

__all__ = [
    "ACTION_SEMANTICS_VERSION",
    "AsyncCanonicalRecorder",
    "CAPTURE_CONTRACT_VERSION",
    "CheckResult",
    "PreflightReport",
    "RecorderState",
    "SESSION_EVENT_SCHEMA_VERSION",
    "ValidationReport",
    "write_c1_session_fixtures",
    "write_cleaning_ready_fixture",
    "run_static_preflight",
    "reconstruct_episode_boundaries",
    "inspect_inprogress",
    "validate_episode",
    "validate_cleaning_ready",
    "write_diagnostic_report",
]
