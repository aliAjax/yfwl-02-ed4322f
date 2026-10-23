#!/usr/bin/env python3
"""Legacy heat-network handover reader.

The old shift left a schema-compatible reader and empty planning fields. The
joint inverse for aggregate heat/temperature observations, branch lag and heat
loss, and the pump selection still remain to be completed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def no_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("input refused: expected input and output paths", file=sys.stderr)
        return 2
    try:
        payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"), object_pairs_hook=no_duplicate)
        if not isinstance(payload, dict):
            raise ValueError("top-level object required")
        output_dir = Path(argv[2])
        output_dir.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 1,
            "run_id": payload.get("run_id", ""),
            "network_id": payload.get("network_id", ""),
            "assignments": [],
            "topology": [],
            "selection": [],
            "summary": {
                "branch_count": len(payload.get("branch_records", [])),
                "total_power_kw": 0.0,
                "trunk_flow_m3h": 0.0,
            },
        }
        (output_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"input refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
