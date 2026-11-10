# Contributing

Use Python 3.12+ and the locked development environment:

```bash
uv sync --frozen --all-extras --group dev
uv run pytest -q
uv run ruff check src tests scripts examples
uv run python scripts/check_publication.py
uv run adapter-vlm-policy --help
```

Keep changes focused. Add tests for changed behavior, particularly context
isolation, cancellation and terminal-state commits. Unit tests must not
require model credentials, a private provider, a Gateway binary or hardware.
Optional real-model/PAOS runs need explicit local configuration; retain
negative cases and failed attempts in local reports.

## Interface documentation

Maintain English `README.md` and Chinese `README_zh.md` separately, with
reciprocal links. Update both, and both developer guides, when changing
interfaces or behavior. Follow the organization's
[Node README contract](https://github.com/PhyAgentOS/forge/blob/master/CONTRIBUTING.md#node-readme-contract):
count services, production Dora Nodes, example Nodes, Endpoints, operations
and deployment Tools separately. Do not imply released binary capabilities
from source-only examples.

## Publication hygiene

Never commit credentials, private hostnames, machine paths, local provider
configuration, recordings, context databases or runtime logs. Run the
publication checks on the source tree and inspect the built source/wheel
archives. Keep dependency changes reflected in `uv.lock` and third-party
notices; report vulnerabilities privately as described in `SECURITY.md`.

By submitting a contribution, you agree to license it under Apache-2.0.
