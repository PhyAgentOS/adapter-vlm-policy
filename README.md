English | [简体中文](README_zh.md)

# adapter-vlm-policy

An Agent-invoked general LLM/VLM inference service. Supply text, images and a response contract; receive text or validated JSON. Optional task contexts preserve explicitly selected history and immutable image evidence between recognition and later verification.

The inference service runs independently of robot sensor dataflows. It does not execute model-generated robot actions. The example MuJoCo executor is a separate test component.

## Node, Endpoint and Tool API

| Item | Implementation |
|---|---|
| Standalone default | One HTTP inference service; zero Dora Nodes or Forge Endpoints |
| Production Dora Nodes | One optional `forge-bridge --profile vlm` provider; [implementation](src/adapter_vlm_policy/forge_bridge.py), launched through the same CLI |
| Auxiliary programs | Example simulation provider, offline benchmark, request client and Skill packager; excluded from production Node count |
| Endpoint | `reasoning.vlm`, supplied by the optional VLM bridge |
| Operations | Two: `capabilities` Query and `infer` Action; descriptor concurrency four per operation, with inference queue/concurrency capped by service configuration |
| Lifecycle | Infer supports status, result and cancellation; accepted control is not a terminal outcome |
| Direct HTTP Tool API | Zero on the Dora provider; Gateway routes Tool calls. Independent service HTTP routes are listed below |
| Example deployment | Three Dora Nodes: external Gateway, VLM bridge and auxiliary simulator bridge; two business Endpoints, five deployment Tools |

The example [Gateway configuration](examples/mujoco_skill/profiles/mujoco/gateway.yaml) binds:

| Tool | Endpoint / operation | Provider |
|---|---|---|
| `vlm.capabilities` | `reasoning.vlm / capabilities` (Query) | VLM bridge |
| `vlm.infer` | `reasoning.vlm / infer` (Action) | VLM bridge |
| `sim.observe` | `sim.tabletop / observe` (Query) | Example simulator bridge |
| `sim.prepare` | `sim.tabletop / prepare` (Action) | Example simulator bridge |
| `sim.execute` | `sim.tabletop / execute` (Action) | Example simulator bridge |

These Tool IDs are example bindings, not additional Endpoints. The [request types](src/adapter_vlm_policy/models.py) use `vlm.infer.request.v1`; results use `vlm.infer.result.v1`. Forge wire compatibility follows the pinned public SDK's `TOOL_ENDPOINT_PROTOCOL`. No prebuilt Node or Gateway binary is distributed here.

## Install and run

Python 3.11+ on POSIX for the service; Python 3.12+ for the optional Forge SDK. Dependency markers skip Forge SDK packages on Python 3.11, and the bridge CLI reports that Python 3.12+ is required. Use a dedicated virtual environment. Windows is not supported by the POSIX storage lock.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp config.example.yaml config.local.yaml
export VLM_API_BASE=https://your-provider.example/v1
export VLM_MODEL=your-vision-capable-model
# Supply VLM_API_KEY through your local environment/credential manager.
.venv/bin/adapter-vlm-policy serve --config config.local.yaml
```

Alternatively configure `provider_file: ~/.llm_config` in a model route. The service reads simple `base_url`, `api_key`, `model_name` and `model_effort` assignments as data, without executing the file. Model and effort selection use explicit route settings, then `VLM_MODEL` / `VLM_REASONING_EFFORT`, then provider-file values. Unset environment placeholders fall back to that file. Standard file aliases `model` and `reasoning_effort` are also supported. Effort is sent as `reasoning_effort` without silently dropping unsupported values or changing models. Capabilities, inference snapshots and successful result provenance expose the effective effort. If the provider file holds a site root rather than an API root, set `api_prefix: v1`. Existing PAOS JSON provider configuration can be imported through `paos_config`; credentials are not copied into reports or capabilities.

Configure `allowed_file_roots` for service-host paths. Loopback is the default. Non-loopback listening requires configured credential-to-namespace mappings; each client uses `Authorization: Bearer ...` and cannot select another namespace through request metadata.

## API

| Route | Purpose |
|---|---|
| GET /health | Process readiness |
| GET /v1/capabilities | Implemented model/media/context capabilities |
| POST /v1/infer | Submit inference, return HTTP 202 and request handle |
| POST /v1/infer:wait | Submit or reconcile, then return terminal result |
| GET /v1/requests/{id} | Status and available result |
| GET /v1/requests/{id}/result | Await/retrieve result |
| POST /v1/requests/{id}/cancel | Cancel this inference |
| GET /v1/requests/{id}/snapshot | Exact assembled context, image refs and repair calls |
| GET /v1/contexts/{id} | Authorized task context/version |
| POST /v1/contexts/{id}/close | Close using expected_version |

Agent-facing Tools are `vlm.infer` and `vlm.capabilities`. Status/cancellation/context inspection are lifecycle APIs, not grasp-specific inference Tools.

```json
{
  "request_id": "question-1",
  "messages": [{"role": "user", "content": "Explain the task briefly."}],
  "limits": {"deadline_ms": 60000, "max_output_tokens": 1200}
}
```

Save a request to `request.json`, then call:

```bash
.venv/bin/adapter-vlm-policy infer request.json --url http://127.0.0.1:19120
```

Image message blocks:

```json
{
  "type": "image",
  "image_id": "before",
  "source": {"type": "file", "path": "/controlled/frames/before.png"},
  "metadata": {"observation_id": "obs-118", "camera_id": "world"}
}
```

Use `source: {"type":"inline_base64","mime_type":"image/png","data":"..."}` for transferred bytes. Task mode also supports `source: {"type":"context_media","media_id":"<returned-media-id>"}`. An observation ID alone does not contain image pixels. There is no camera subscription or implicit latest-image fallback. Original pixels are retained; no implicit crop/resize alters coordinates.

`response_format` defaults to text. For structured results, use `{"type":"json_schema","schema":{...}}` or a configured `schema_id`. Only visible model content is parsed; reasoning streams are not used as answers. Invalid output gets bounded repair, then a structured error. Shape validation does not establish semantic or physical correctness.

## One task, multiple calls

1. First infer: `context = {"mode":"task","task_id":"task-7","expected_version":0,"updates":{"goal":"...","constraints":["..."]}}`.
2. Read returned context_id, version, output.record_id and media IDs.
3. Agent invokes its execution tools separately, then supplies the actual reports and new image in another infer.
4. Continue with context_id, task_id, expected_version, explicit `selection.record_ids`, and `updates`.
5. Reattach a historical image explicitly with context_media when comparison needs its pixels. Selected historical records do not automatically resend images or activate old system prompts.

A successful turn advances the version once and stores model output as a hypothesis. Failed/cancelled/timed-out inference does not commit staged task updates. Request IDs deduplicate immutable payloads; conflicting reuse is rejected. One writer per task context prevents interleaved histories; stale versions and concurrent writes return explicit errors. Main branch only; automatic summaries and branch forks are not advertised.

SQLite records and immutable media survive restarts. Interrupted in-flight requests become SERVICE_RESTARTED, never silent successful commits. One service process owns each data directory. Idle TTL reclaims context text/media and makes recovery explicitly expire; active requests pin their media. Queue, image, output, deadline and context budgets are bounded. Oversized context is rejected rather than silently discarding evidence.

## PAOS + Gateway + MuJoCo example

Install test dependencies and compatible Forge SDK:

```bash
.venv/bin/python -m pip install -e '.[test,forge]'
.venv/bin/python scripts/build_demo_skill.py
```

The demo has Gateway, an inference control bridge and a separate simulation control bridge. Each bridge manages its own HTTP child service; neither subscribes to camera streams. Agent supplies images to inference. The MuJoCo executor uses real contact physics with a parallel gripper, and exposes observation, geometric preparation and bounded simulation primitives. Privileged evaluation is not exposed as an Agent Tool.

Before starting, set:

```bash
export ADAPTER_VLM_PYTHON=/absolute/project/.venv/bin/python
export ADAPTER_VLM_WORKSPACE=/absolute/local/demo-output
export GATEWAY_BINARY=/absolute/path/to/compatible/gateway
# This profile reads base_url, api_key, model_name and model_effort from ~/.llm_config.
paos skill install dist/vlm-mujoco-demo-0.1.2.tar.gz --local --yes
paos skill start vlm-mujoco-demo --profile mujoco
paos skill status vlm-mujoco-demo
paos skill stop vlm-mujoco-demo
```

The demo launcher clears inherited VLM model/effort environment overrides, preventing a shared Dora daemon's stale values from replacing provider-file settings. For a deliberate demo override, edit the explicit model/reasoning_effort route settings in its service YAML. The standalone service keeps the precedence described above.

Use an isolated PAOS instance when another robot runtime is already active. `scripts/isolated_paos.py` uses the official configuration-path mechanism; set ADAPTER_PAOS_CONFIG to a separate configuration file, then run the same `skill ...` commands with the PAOS interpreter. It does not stop the shared Dora coordinator or unrelated flows.

## Validation

```bash
.venv/bin/python -m pytest -q --durations=10
MUJOCO_GL=egl .venv/bin/python -m adapter_vlm_policy.benchmark \
  --config config.local.yaml --output reports/my-run
# After actual PAOS Skill startup:
.venv/bin/python scripts/gateway_acceptance.py --output reports/my-gateway-run
```

For reasoning models, set an explicit adequate output budget in both deployment limits and the request. The benchmark accepts `--max-output-tokens 8192 --deadline-ms 180000`; the Gateway script accepts `--max-output-tokens 8192`. These flags do not change the configured effort. A token-limit response is reported as truncated even when no visible text was generated.

The benchmark uses a real configured model, rendered RGB-D, independently scored segmentation, model-selected image points, and external MuJoCo execution. It includes negative action/task checks and reports latency, usage, native evaluation and source evidence. Reports distinguish mocked unit tests from real-model tests. This is an initial tabletop gantry/gripper benchmark, not demonstrated arbitrary-object or general robot-arm grasping.

See the [validation summary](docs/VALIDATION.md), [developer guide](DEVELOPING.md), and example Skill instructions for implementation scope and reproduction. Detailed run records remain local. Never publish credential files or internal run records unintentionally.

## License and contribution

Apache-2.0: [LICENSE](LICENSE), [NOTICE](NOTICE), [third-party notices](THIRD_PARTY_NOTICES.md). See [contributing](CONTRIBUTING.md), [security policy](SECURITY.md) and the [open-source audit](OPEN_SOURCE_AUDIT.md). For reproducible development, use `uv sync --frozen --all-extras --group dev`; dependencies are locked to public PyPI sources.
