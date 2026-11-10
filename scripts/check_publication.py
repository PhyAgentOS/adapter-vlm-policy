"""Fail publication on private configuration, sensitive paths or missing notices."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "OPEN_SOURCE_AUDIT.md",
    "README.md",
    "README_zh.md",
    "uv.lock",
    ".secrets.baseline",
    "LICENSES/forge-tool-Apache-2.0.txt",
    "LICENSES/forge-msgs-Apache-2.0.txt",
)
LOCAL_DIRS = {
    ".git",
    ".venv",
    ".audit",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}
PATTERNS = (
    ("private host", re.compile(r"\bgitlab\.ex-ai\.cn\b", re.I)),
    (
        "private IPv4",
        re.compile(
            r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
        ),
    ),
    ("personal absolute path", re.compile(r"/(?:Users|home)/(?!<|\$)[A-Za-z0-9_.-]+/")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    (
        "access token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{15,}|sk-[A-Za-z0-9_-]{20,})\b"
        ),
    ),
)


def paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=False
    )
    if result.returncode == 0 and result.stdout:
        return [root / name.decode() for name in result.stdout.split(b"\0") if name]
    return [
        p
        for p in root.rglob("*")
        if p.is_file() and not LOCAL_DIRS.intersection(p.relative_to(root).parts)
    ]


def inspect_text(text: str) -> list[str]:
    return [label for label, pattern in PATTERNS if pattern.search(text)]


def check(root: Path = ROOT) -> list[str]:
    findings = [
        f"Missing required file: {name}" for name in REQUIRED if not (root / name).is_file()
    ]
    for path in paths(root):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(f"Symlink requires review: {name}")
            continue
        if path.stat().st_size > 5 * 1024 * 1024:
            findings.append(f"Unexpected large source file: {name}")
        if name.startswith((".runtime", ".venv/")) or (
            name.startswith("reports/") and name != "reports/.gitkeep"
        ):
            findings.append(f"Local runtime data: {name}")
        if path.name in {
            "config.local.yaml",
            "config.test.yaml",
            ".llm_config",
            ".env",
        } or path.suffix in {".db", ".sqlite3", ".pem", ".key", ".log"}:
            findings.append(f"Private file type: {name}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"Unexpected binary source file: {name}")
            continue
        findings.extend(f"{label}: {name}" for label in inspect_text(text))
    provenance = root / "src/adapter_vlm_policy/_vendor/PROVENANCE.json"
    if provenance.is_file():
        for entry in json.loads(provenance.read_text())["files"]:
            path = provenance.parent / entry["path"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["vendored_sha256"]:
                findings.append(f"Vendored file changed without provenance review: {entry['path']}")
    return findings


if __name__ == "__main__":
    failures = check()
    if failures:
        raise SystemExit("\n".join(failures))
    print("Publication checks passed: notices present; no private source data detected.")
