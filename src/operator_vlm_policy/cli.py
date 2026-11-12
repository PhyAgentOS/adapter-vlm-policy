from __future__ import annotations

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(prog="operator-vlm-policy")
    subs = parser.add_subparsers(dest="command", required=True)
    serve = subs.add_parser("serve", help="Run the independent Agent-invoked service")
    serve.add_argument("--config", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=19120)
    infer = subs.add_parser("infer", help="Send a JSON request to a running service")
    infer.add_argument("request_file")
    infer.add_argument("--url", default="http://127.0.0.1:19120")
    infer.add_argument("--token-env", default="VLM_SERVICE_TOKEN")
    bridge = subs.add_parser(
        "forge-bridge",
        help="Run optional Gateway control-plane bridge; no sensor inputs",
    )
    bridge.add_argument("--url", default="http://127.0.0.1:19120")
    bridge.add_argument("--token-env", default="VLM_SERVICE_TOKEN")
    bridge.add_argument("--service-config")
    bridge.add_argument("--profile", choices=["vlm", "simulation"], default="vlm")
    bridge.add_argument("--spawn-command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        from .api import create_app
        from .config import Settings

        settings = Settings.load(args.config)
        if args.host not in ("127.0.0.1", "localhost", "::1") and not settings.clients:
            parser.error(
                "Configure client credentials before listening on a non-loopback interface"
            )
        # One process per SQLite store; inference concurrency is handled internally.
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
    elif args.command == "infer":
        import os

        import httpx

        token = os.environ.get(args.token_env)
        headers = {"Authorization": "Bearer " + token} if token else {}
        response = httpx.post(
            args.url + "/v1/infer:wait",
            json=json.loads(Path(args.request_file).read_text()),
            headers=headers,
            timeout=200,
        )
        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    elif args.command == "forge-bridge":
        import sys

        if sys.version_info < (3, 12):
            parser.error("The Forge bridge requires Python 3.12+ and the [forge] extra")
        from .forge_bridge import run

        run(
            args.url,
            args.token_env,
            args.service_config,
            args.profile,
            args.spawn_command,
        )


if __name__ == "__main__":
    main()
