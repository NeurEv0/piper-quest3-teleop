#!/usr/bin/env python3
"""Validate one finalized Piper MCAP episode and print a JSON report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mcap_log.validator import validate_mcap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="raw.mcap or its containing episode directory")
    parser.add_argument("--allow-no-cameras", action="store_true")
    args = parser.parse_args()
    path = args.path / "raw.mcap" if args.path.is_dir() else args.path
    report = validate_mcap(path, require_cameras=not args.allow_no_cameras)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
