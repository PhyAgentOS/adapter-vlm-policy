import importlib.util
from pathlib import Path


def test_reviewed_checksum_does_not_allow_new_secret_in_same_file():
    path = Path(__file__).resolve().parents[1] / "scripts/check_secret_findings.py"
    spec = importlib.util.spec_from_file_location("secret_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    known = {"type": "Hex High Entropy String", "hashed_secret": "known", "is_secret": False}
    new = {"type": "Hex High Entropy String", "hashed_secret": "changed"}
    baseline = {"results": {"provenance.json": [known]}}
    assert module.unreviewed(baseline, baseline) == []
    assert module.unreviewed({"results": {"provenance.json": [known, new]}}, baseline) == [
        "provenance.json"
    ]
