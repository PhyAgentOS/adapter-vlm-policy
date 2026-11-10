# Changelog

## 0.1.2

- Prepare the first audited GitHub source snapshot under Apache-2.0, with
  notices, public dependency locks, publication checks and CI.
- Require the patched Pillow 13 release. Isolate necessary licensed Forge
  carriers instead of installing an image-package dependency that excludes it.
- Document and test the Python 3.11 service / Python 3.12 Forge boundary.
- Keep private deployment information, provider aliases and prior internal
  history outside the public source snapshot.

## 0.1.1

- Honor provider-file model_name/model_effort and forward reasoning_effort.
- Expose effective model settings and prevent stale demo-model overrides.

## 0.1.0

- Introduce generic text/image inference, validated JSON output, optional
  isolated task context, lifecycle APIs and a separate MuJoCo Skill example.
