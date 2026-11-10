import importlib.util
from pathlib import Path


def publication_module():
    path = Path(__file__).resolve().parents[1] / "scripts/check_publication.py"
    spec = importlib.util.spec_from_file_location("publication", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publication_scan_rejects_private_inputs_without_printing_their_values():
    scan = publication_module()
    private_host = ".".join(["gitlab", "ex-ai", "cn"])
    assert scan.inspect_text("https://" + private_host) == ["private host"]
    assert scan.inspect_text("/" + "Users" + "/person/credentials") == ["personal absolute path"]
    assert scan.inspect_text("sk-" + "a" * 30) == ["access token"]
    assert scan.inspect_text("https://provider.example/v1") == []


def test_publication_scan_catches_runtime_files_and_missing_notices(tmp_path):
    scan = publication_module()
    (tmp_path / "config.local.yaml").write_text("model: private-model\n")
    findings = scan.check(tmp_path)
    assert "Private file type: config.local.yaml" in findings
    assert "Missing required file: LICENSE" in findings
