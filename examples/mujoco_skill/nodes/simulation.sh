#!/bin/sh
set -eu
exec "$ADAPTER_VLM_PYTHON" -m adapter_vlm_policy.cli forge-bridge --url http://127.0.0.1:19121 --profile simulation --spawn-command "$ADAPTER_VLM_PYTHON" "$PAOS_SKILL_ROOT/simulation_service.py" --scene "$PAOS_SKILL_ROOT/assets/scene.xml" --output "$ADAPTER_VLM_WORKSPACE/frames" --port 19121
