"""Build a deterministic Skill archive with PAOS's mandatory per-file manifest."""

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path


def build(output="dist"):
    root = Path(__file__).resolve().parents[1]
    source = root / "examples/mujoco_skill"
    folder = root / output
    folder.mkdir(exist_ok=True, parents=True)
    target = folder / "vlm-mujoco-demo-0.1.2.tar.gz"
    files = []
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise ValueError("Skill source must not contain symlinks")
        if (
            path.is_file()
            and "__pycache__" not in path.parts
            and path.name != "archive-manifest.json"
        ):
            files.append(
                (
                    path.relative_to(source).as_posix(),
                    path.read_bytes(),
                    0o755 if path.stat().st_mode & 0o111 else 0o644,
                )
            )
    # The source-only Skill redistributes project code and must retain its license/notices.
    existing = {name for name, _, _ in files}
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
        if name not in existing:
            files.append((name, (root / name).read_bytes(), 0o644))
    manifest = {
        "schema_version": 1,
        "files": [
            {
                "path": name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for name, data, _ in files
        ],
    }
    files.append(
        (
            "archive-manifest.json",
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode(),
            0o644,
        )
    )
    with target.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for name, data, mode in files:
                    entry = tarfile.TarInfo(name)
                    entry.size = len(data)
                    entry.mode = mode
                    entry.mtime = 0
                    archive.addfile(entry, io.BytesIO(data))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist")
    print(build(parser.parse_args().output))
