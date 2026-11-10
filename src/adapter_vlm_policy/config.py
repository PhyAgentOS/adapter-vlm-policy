from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ModelRoute:
    model: str
    base_url: str
    api_key: str = field(default="", repr=False)
    vision: bool = True
    native_json_schema: bool = False
    context_tokens: int = 32000
    max_images: int = 8
    reasoning_effort: str | None = None


def _environment_setting(value: str | None) -> str:
    """Resolve optional environment references before falling back to provider data."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("Model route settings must be strings")
    return re.sub(
        r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))",
        lambda match: os.environ.get(match[1] or match[2], ""),
        value,
    ).strip()


@dataclass
class Settings:
    data_dir: Path
    allowed_file_roots: list[Path] = field(default_factory=list)
    routes: dict[str, ModelRoute] = field(default_factory=dict)
    default_route: str = "default"
    max_concurrency: int = 2
    max_pending: int = 8
    max_deadline_ms: int = 180000
    max_output_tokens: int = 4096
    max_image_bytes: int = 10 * 1024 * 1024
    max_request_bytes: int = 40 * 1024 * 1024
    max_image_pixels: int = 20_000_000
    max_images: int = 8
    context_ttl_s: int = 86400
    max_schema_bytes: int = 32000
    schema_repair_attempts: int = 1
    # token -> namespace, resolved only from deployment environment
    clients: dict[str, str] = field(default_factory=dict, repr=False)
    schemas: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Settings:
        config_path = Path(path).resolve()
        raw: dict[str, Any] = yaml.safe_load(config_path.read_text()) or {}
        base = config_path.parent

        def local(value: str) -> Path:
            p = Path(os.path.expandvars(value)).expanduser()
            return (base / p).resolve() if not p.is_absolute() else p.resolve()

        routes = {}
        for name, value in raw.get("model_routes", {}).items():
            cfg = dict(value)
            provider = {}
            provider_file = cfg.pop("provider_file", None)
            if provider_file:
                import shlex

                # Read simple assignments as data. Never source/execute credential files.
                for line in local(provider_file).read_text().splitlines():
                    line = line.strip()
                    if "=" in line and not line.lstrip().startswith("#"):
                        k, v = line.removeprefix("export ").split("=", 1)
                        parts = shlex.split(v.strip(), comments=True)
                        provider[k.strip()] = parts[0] if parts else ""
            key = os.environ.get(cfg.pop("api_key_env", "VLM_API_KEY"), "") or provider.get(
                "api_key", ""
            )
            cfg["base_url"] = (
                _environment_setting(cfg.get("base_url"))
                or _environment_setting(os.environ.get("VLM_API_BASE"))
                or provider.get("base_url", "")
            )
            prefix = cfg.pop("api_prefix", "")
            if prefix:
                cfg["base_url"] = cfg["base_url"].rstrip("/") + "/" + prefix.strip("/")
            cfg["model"] = (
                _environment_setting(cfg.get("model"))
                or _environment_setting(os.environ.get("VLM_MODEL"))
                or provider.get("model_name")
                or provider.get("model", "")
            )
            cfg["reasoning_effort"] = (
                _environment_setting(cfg.get("reasoning_effort"))
                or _environment_setting(os.environ.get("VLM_REASONING_EFFORT"))
                or provider.get("model_effort")
                or provider.get("reasoning_effort")
                or provider.get("effort")
                or None
            )
            routes[name] = ModelRoute(api_key=key, **cfg)
        # Explicit optional PAOS configuration import; never print/write credentials.
        if raw.get("paos_config"):
            import json

            paos = json.loads(local(raw["paos_config"]).read_text())
            provider = paos.get("providers", {}).get(raw.get("paos_provider", "custom"), {})
            routes["paos"] = ModelRoute(
                model=raw.get("paos_model") or provider.get("defaultModel", ""),
                base_url=provider.get("apiBase", ""),
                api_key=provider.get("apiKey", ""),
            )
        clients = {}
        for client in raw.get("clients", []):
            token = os.environ.get(client["token_env"], "")
            if not token:
                raise ValueError(
                    f"Missing client credential environment variable {client['token_env']}"
                )
            if token in clients:
                raise ValueError("Duplicate client credential")
            clients[token] = client["namespace"]
        schemas = {}
        for name, rel in raw.get("schemas", {}).items():
            import json

            schemas[name] = json.loads(local(rel).read_text())
        limits = raw.get("limits", {})
        return cls(
            data_dir=local(raw.get("data_dir", ".runtime")),
            allowed_file_roots=[local(v) for v in raw.get("allowed_file_roots", [])],
            routes=routes,
            default_route=raw.get("default_route", "default"),
            clients=clients,
            schemas=schemas,
            **limits,
        )
