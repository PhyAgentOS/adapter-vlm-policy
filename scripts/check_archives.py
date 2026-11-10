"""Inspect source/wheel contents before uploading artifacts."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from check_publication import inspect_text


def check(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            entries = [
                (name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")
            ]
    else:
        with tarfile.open(path) as archive:
            entries = [
                (entry.name, archive.extractfile(entry).read())
                for entry in archive.getmembers()
                if entry.isfile()
            ]
    findings = []
    for name, content in entries:
        parts = Path(name).parts
        if any(part in {".git", ".venv", ".audit", "__pycache__", ".runtime"} for part in parts):
            findings.append(f"Local data in archive: {name}")
        if Path(name).name in {"config.local.yaml", "config.test.yaml", ".llm_config", ".env"}:
            findings.append(f"Private configuration in archive: {name}")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            findings.append(f"Unexpected binary file in source distribution: {name}")
            continue
        findings.extend(f"{label}: {name}" for label in inspect_text(text))
    if not any("LICENSE" in name for name, _ in entries):
        findings.append("Missing distribution license")
    return findings


if __name__ == "__main__":
    packages = [*Path("dist").glob("*.whl"), *Path("dist").glob("*.tar.gz")]
    if not packages:
        raise SystemExit("Build source and wheel packages first")
    failures = [f"{package.name}: {finding}" for package in packages for finding in check(package)]
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Distribution checks passed for {len(packages)} source/wheel archives.")
