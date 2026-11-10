import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path


def test_source_skill_retains_license_and_deterministic_manifest(tmp_path):
    source = Path(__file__).resolve().parents[1] / "scripts/build_demo_skill.py"
    spec = importlib.util.spec_from_file_location("skill_builder", source)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    first = builder.build(str(tmp_path / "first"))
    second = builder.build(str(tmp_path / "second"))
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        manifest = json.load(archive.extractfile("archive-manifest.json"))
        entries = {entry["path"]: entry for entry in manifest["files"]}
        assert {"LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"} <= entries.keys()
        for name, entry in entries.items():
            assert hashlib.sha256(archive.extractfile(name).read()).hexdigest() == entry["sha256"]
