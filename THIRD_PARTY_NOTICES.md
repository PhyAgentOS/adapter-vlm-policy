# Third-party notices

Original project code, tests, configuration, documentation and the procedural
MuJoCo scene are licensed under Apache License 2.0. The narrowly scoped Forge
carrier components below retain their upstream Apache-2.0 license. No robot
assets, recorded images, model weights or executables are vendored.

## Direct dependencies

| Distribution | Role | Declared license |
|---|---|---|
| FastAPI | HTTP API | MIT |
| Uvicorn | HTTP server | BSD-3-Clause |
| HTTPX | Model and service HTTP client | BSD-3-Clause |
| Pydantic | Request validation | MIT |
| jsonschema | Structured-output validation | MIT |
| Pillow | Image decoding and validation | MIT-CMU; bundled codec notices may also apply |
| PyYAML | Configuration parsing | MIT |
| forge-tool | Optional Forge control bridge | Apache-2.0 |
| dora-rs | Optional Dora runtime bindings | MIT |
| PyArrow | Optional Arrow carriers | Apache-2.0 |
| MuJoCo | Optional simulation tests | Apache-2.0 |
| NumPy | Optional simulation geometry | BSD-3-Clause |
| pytest | Tests | MIT |
| pytest-asyncio | Async tests | Apache-2.0 |

The core `forge-tool==1.0.0` package is an external public-index dependency.
Its optional `dora` extra is not used: the full `forge-msgs` distribution
constrains Pillow below the patched release required by this project.
See the public [Forge project](https://github.com/PhyAgentOS/forge) and the
installed distribution metadata for external dependency notices.
Transitive dependencies and native libraries retain their own notices; use
`uv.lock` and installed metadata to inspect the complete graph.

## Vendored Forge carrier subset

Source: public PyPI distributions [forge-tool 1.0.0](https://pypi.org/project/forge-tool/1.0.0/)
and [forge-msgs 1.3.0](https://pypi.org/project/forge-msgs/1.3.0/), both Apache-2.0.
Copied modules live under `src/operator_vlm_policy/_vendor/`, without shadowing
either upstream package:

- `dora_binding.py`: `forge_tool.dora`; imports point to the external core SDK
  and local carrier. Two Python 3.12 type-alias statements use equivalent
  assignment syntax so base-service Python 3.11 source compilation works.
- `tool_message.py`: `forge_msgs.tool`; its Arrow-normalizer import points to
  the local helper.
- `tool_arrow.py`: only `forge_msgs.arrow.ensure_record_batch`, unchanged,
  with its PyArrow import. No image codecs or unrelated message classes are
  copied.

Protocol behavior is unchanged. Source/redistributed hashes and distribution
versions are recorded in `_vendor/PROVENANCE.json`. Original license texts
are retained in `LICENSES/forge-tool-Apache-2.0.txt` and
`LICENSES/forge-msgs-Apache-2.0.txt`, included in source and wheel packages.
Compatibility tests roundtrip registration envelopes through Arrow carriers
and exercise payload-size rejection. Update this subset only with explicit
provenance and compatibility checks.

## Build and audit tools

Hatchling (MIT) builds the source/wheel distributions. Ruff (MIT), Build (MIT),
and detect-secrets (Apache-2.0) are development tools.
uv (Apache-2.0 OR MIT) manages the reproducible environment. They are not
vendored into this package.

The example Skill archive contains only this project's source and procedural
scene. It requires separately installed PAOS, Gateway, Forge and MuJoCo
components. Model services, weights and submitted media are not distributed.
Publishing a bundled executable would require a separate review of its
complete contents, including native codec and library notices.
