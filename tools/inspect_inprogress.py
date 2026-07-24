#!/usr/bin/env python3
"""Inspect preserved .inprogress episodes and emit a cleaning diagnostic report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from canonical_raw.recovery import inspect_inprogress, write_diagnostic_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = write_diagnostic_report(args.output_root, args.report) if args.report else inspect_inprogress(args.output_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
