#!/bin/sh
set -eu
exec "$GATEWAY_BINARY" --config "$PAOS_SKILL_ROOT/profiles/mujoco/gateway.yaml"
