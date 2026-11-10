"""Reject new findings; allow only exact, previously reviewed non-secret hashes."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def unreviewed(current: dict, baseline: dict) -> list[str]:
    reviewed = {
        (name, finding["type"], finding["hashed_secret"])
        for name, findings in baseline.get("results", {}).items()
        for finding in findings
        if finding.get("is_secret") is False
    }
    return [
        name
        for name, findings in current.get("results", {}).items()
        for finding in findings
        if (name, finding["type"], finding["hashed_secret"]) not in reviewed
    ]


if __name__ == "__main__":
    current = json.loads(Path(sys.argv[1]).read_text())
    baseline = json.loads(Path(".secrets.baseline").read_text())
    failures = sorted(set(unreviewed(current, baseline)))
    if failures:
        raise SystemExit("Unreviewed potential secrets in: " + ", ".join(failures))
    print("Secret scan passed: zero unreviewed findings.")
