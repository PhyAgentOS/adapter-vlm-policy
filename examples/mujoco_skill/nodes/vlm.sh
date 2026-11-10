#!/bin/sh
set -eu
# This demo selects model/effort from provider_file, not a shared daemon's old environment.
unset VLM_MODEL VLM_REASONING_EFFORT
exec "$ADAPTER_VLM_PYTHON" -m adapter_vlm_policy.cli forge-bridge --url http://127.0.0.1:19120 --service-config "$PAOS_SKILL_ROOT/profiles/mujoco/service.yaml"
