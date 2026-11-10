# Open-source release audit

- Date: 2026-11-10
- Repository: `PhyAgentOS/adapter-vlm-policy`
- Source version: 0.1.2
- Scope: source/wheel packages and the source-only example Skill; no model
  weights, recorded media, Gateway binary or hardware controller is bundled.

## Publication decision and history

The prepared source snapshot follows the organization's Apache-2.0 node
publication model. It includes LICENSE, NOTICE, dependency/provenance notices,
security/contribution policies, bilingual interface guides and public CI.
The previous internal Git graph, machine-specific commit email, operator
diagnostic launcher, runtime configuration and raw test evidence are not
carried into the new GitHub history.

The target repository was empty during preparation. GitHub rejected a fork
with "Empty repositories cannot be forked." The intended publication route
is a minimal default-branch bootstrap followed by a personal-fork PR for the
audited source, subject to owner authorization for that one bootstrap write.
No development branch is pushed to the organization repository.

## Dependency finding and remediation

Fresh public-index resolution originally selected Pillow 12.3.0 because the
full `forge-msgs` package requires `pillow<13`. The vulnerability audit found
39 advisories for that version, fixed in Pillow 13.0.0. They were not ignored.

This snapshot requires `Pillow>=13,<14`, uses the public `forge-tool==1.0.0`
core package without its `dora` extra, and includes only the necessary
Apache-2.0 Arrow/Dora carrier components from public package distributions.
Their import adaptations and equivalent type-alias syntax are documented;
protocol logic is unchanged. Source hashes, distribution versions and public
wheel hashes are retained in `_vendor/PROVENANCE.json`. Both original license
texts are distributed. No private SDK source or model code is copied.

All dependencies resolve from public PyPI. The locked environment contains no
private index, local dependency path or VCS checkout. Python markers preserve
the base service on Python 3.11 while requiring Python 3.12+ for the optional
Forge bridge; the CLI explains unsupported bridge startup.

## Verification record

The final readiness checks cover:

- Locked public installation, tests, CLI startup/help and source compilation.
- Forge registration-envelope roundtrips through RecordBatch, Table,
  StructArray and IPC carriers, plus oversized-payload rejection.
- MuJoCo scene compilation/physics stepping without rendering or hardware.
- Ruff, source/publication scanning and credential-pattern scanning.
- Known-advisory scan of locked runtime and optional dependencies.
- Source/wheel license metadata and contents; source-only Skill packaging.
- Vendored file hashes and public wheel provenance.

Executed readiness results:

- Python 3.12: 41 tests passed, including optional Forge carrier checks.
- Python 3.11: 36 tests passed and the optional Forge test module was skipped,
  as expected; base-service and headless-physics checks ran.
- Ruff and compilation passed on the source snapshot.
- Publication checks found no private configuration, host/path or credential
  patterns; vendored hashes matched recorded provenance.
- detect-secrets found zero unreviewed findings. Thirteen reviewed false
  positives are exact public checksums, environment-variable names and dummy
  test values. The baseline does not suppress a whole file or plugin; a new
  value in the same file fails the check.
- Both locked runtime branches passed the known-advisory scan with no ignored
  advisories after upgrading Pillow.
- Source distribution, wheel and deterministic source-only Skill built; all
  three archive contents passed the private-data/license checks.

Public CI is configured to repeat these checks without model credentials.
Historical real-model/PAOS evidence in `docs/VALIDATION.md` is explicitly
separate from public automated tests.

## Boundaries

Loopback is the default. External deployment requires authenticated access
and operator-provided transport/network controls; see SECURITY.md. Namespace
isolation is not a guarantee against a compromised host or filesystem.
Model outputs and copied protocol envelopes remain untrusted inputs.

This source audit is not a claim that every native library inside externally
installed wheels is vulnerability-free. Bundled executable releases would
need a separate artifact-level license/security review. No such executable
release is part of this import. General-object grasping and physical robot
safety are not established by the procedural simulation example.

## Reference conventions

- [Sim-control policy audit](https://github.com/PhyAgentOS/operator-sim-control-policy/blob/master/OPEN_SOURCE_AUDIT.md)
- [Gateway audit](https://github.com/PhyAgentOS/adapter-forge-gateway/blob/master/OPEN_SOURCE_AUDIT.md)
- [LeRobot runner notices](https://github.com/PhyAgentOS/operator-lerobot-runner/blob/master/THIRD_PARTY_NOTICES.md)
- [Forge Node README contract](https://github.com/PhyAgentOS/forge/blob/master/CONTRIBUTING.md#node-readme-contract)
