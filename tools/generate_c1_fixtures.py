#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from canonical_raw.fixtures import write_c1_session_fixtures


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic no-hardware C1 session fixtures.")
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    for path in write_c1_session_fixtures(args.output_root):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
