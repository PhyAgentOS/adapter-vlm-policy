English | [简体中文](DEVELOPING_zh.md)

# Developer guide

## Components

- `models.py`: strict public request types; optional stateless/task context; text/file/inline/context-media inputs.
- `config.py`: deployment routes, limits, registered schemas and credential resolution. Credential files are parsed, not executed. Model/effort precedence is explicit route → environment → provider file. `model_name`/`model_effort` are honored; configured effort is sent as `reasoning_effort`.
- `store.py`: SQLite WAL transactions, process ownership, context reservations, request deduplication, immutable media, versioned records and expiry.
- `service.py`: request normalization, explicit history selection, budget checks, asynchronous inference, schema repair, snapshots and terminal commit.
- `backend.py`: OpenAI-compatible chat transport. General adapter only; no robot action adapter or model tool execution.
- `api.py`: bounded HTTP bodies, authenticated namespaces and lifecycle routes.
- `forge_bridge.py`: optional control-plane proxy, no sensor inputs. It forwards inference requests to the separate service and maps invocation lifecycle.
- `mujoco_scene.py`, `benchmark.py`, `examples/mujoco_skill/simulation_service.py`: external acceptance harness, not inference behavior.

## State transitions

```text
request: queued → running → succeeded / failed / cancelled / timed_out
context: active(version N) → successful atomic commit(version N+1)
         active → closed / expired
```

A task write reserves its context before inference. Staged Agent updates and the model turn commit together only after valid output. Model conclusions are stored as `model_hypothesis`, not merged into Agent facts. Task identifiers never grant access: namespace comes from the service credential, and every media/record/context lookup validates it.

Each inference freezes source pixels, canonical payload hash, selected records, parent version and assembled model input. Historical system prompts are not replayed as active instructions. Historical image pixels are expanded only when explicitly reattached. Snapshot backend_calls includes repair prompts; raw image bytes are represented by immutable media refs and hashes.

The request deadline covers preparation, queue and model/validation work. Context-size checks use a conservative estimate; no evidence is silently truncated. Budget limits are service-capped. Native JSON generation is optional; local schema validation is mandatory when requested. External schema references are rejected.

## Concurrency and recovery

Use one process per data directory. The advisory lock prevents a second service from marking another live service's requests interrupted. SQLite transactions serialize context admission/commit; each task has one in-flight writer. Stateless calls use separate media containers.

Idempotency is scoped to namespace/request_id and resolved image-content payload hashes. An overwritten file cannot silently turn an identical request ID into a new inference. Status queries do not create new requests. Restart invalidates interrupted requests and releases their context reservations; successful history/media remains available until retention expires.

Cancellation first records a terminal outcome and invalidates ownership; later model answers cannot commit. The backend may continue billing if it does not support abort. This does not stop a robot. The separate simulation executor checks cancellation at physics-step boundaries and reports its own terminal result.

## Forge bridge integration

The bridge is optional. The base service uses no Dora/Forge packages. A PAOS Skill can launch a control-only bridge whose managed subprocess runs the HTTP service. Image blocks arrive through Agent calls, not camera subscriptions.

The bridge uses the public core Forge Tool SDK and a licensed carrier subset under `_vendor`; it does not install the full Forge image-message package. This keeps the image service on patched Pillow 13 without overriding dependency constraints. Preserve the source/license hashes in `_vendor/PROVENANCE.json` and run the carrier compatibility checks when updating it. See THIRD_PARTY_NOTICES.md for the exact import-only adaptations.

Endpoint is `reasoning.vlm`, with capabilities Query and infer Action. Service status/result/cancel map to the Forge invocation lifecycle. Reserve the bridge's execution record **before awaiting HTTP admission**: an immediate Gateway result poll must see pending, not not_found. The acceptance script polls immediately and exercises this race.

Registration leases renew at one third of the Gateway-provided TTL. The Dora main thread handles events and drains an output queue; inference and HTTP dispatch run on a background asyncio loop, preserving lease/cancel responsiveness. The thread holding the Dora Node is the only writer to its outputs.

The demo simulation endpoint is separate (`sim.tabletop`) with observe Query and prepare/execute Actions. Its model-generated JSON primitive plan is checked by the external harness/executor; this is not a robot-action output mode of the LLM service.

## Tests and evidence

Unit tests use an explicit test backend and make no capability claims about a real model. They cover pinning before/after images, fresh schema/instructions, independent tasks/namespaces, cross-context reference rejection, versions, duplicate requests, cancellation races, queue deadlines, schema repair audit, restart, TTL and storage ownership.

The real benchmark uses the configured provider, actual MuJoCo RGB-D and a contact-based parallel gripper. Simulation poses/segmentation are used only by the independent evaluator. Online geometry backprojects model-selected image points using measured depth. There is no fallback to oracle coordinates or attachment welds. Keep failed attempts as well as passing runs.

Disable multisample antialiasing for the segmentation benchmark (`offsamples=0`). Integer ID colors blended across edges otherwise contaminate masks with other objects and invalidate bounding-box scoring. Do not respond by relaxing model thresholds.

Model tests include action-not-executed and task-only-lifted negative controls. Invocation success and semantic/native success are recorded separately. Report full planning, recognition/grasp, action compilation, action review, task review and end-to-end latency; unknown/failed cases remain visible.

## Reproducible commands

```bash
.venv/bin/python -m pytest -q --durations=10 --junitxml=reports/unit-tests.xml
.venv/bin/python -m compileall -q src examples scripts tests
.venv/bin/python scripts/build_demo_skill.py
MUJOCO_GL=egl .venv/bin/python -m operator_vlm_policy.benchmark \
  --config config.local.yaml --output reports/acceptance
.venv/bin/python scripts/gateway_acceptance.py --output reports/gateway
```

Capture interpreter, installed package versions, source hashes, Gateway digest, model route/resolved model, scene seed, request IDs, timestamps and raw structured results locally. Do not include credentials or unredacted private provider responses in reports. Token usage is provider-reported; monetary cost remains unknown without actual price/billing evidence.

## Current limitations

- One main context branch; no automatic history compaction or branch merge. Over-budget context is rejected.
- One service process per SQLite store; no distributed context locking.
- Inline/file/context-media only; multipart uploads and remote arbitrary URL fetches are not implemented.
- No full arbitrary-object grasping claim. The initial scene is a tabletop gantry with a parallel gripper; general robot-arm collision planning and broader asset distributions require separate validation.
- Gateway wire size can be lower than the service's HTTP media limit. Use controlled same-host image paths or appropriately compressed inline images when routing through Gateway.
- The example runtime requires explicit PAOS, Dora, compatible Gateway and Forge SDK prerequisites. A source-checkout installation is valid evidence only when exact versions are recorded and actual interoperability tests pass.
