English | [简体中文](VALIDATION_zh.md)

# Validation scope

## Public automated checks

The unit suite uses an explicit in-memory test model backend and local temporary
files. It checks task/namespace isolation, immutable media, explicit history,
version conflicts, deduplication, cancellation, deadlines, JSON repair,
restart/TTL, model/effort configuration and HTTP forwarding. These tests do not
establish the capability of a particular model provider.

Release checks also compile and step the procedural MuJoCo scene without a
renderer, verify the Forge Python-version gate, inspect publication hygiene,
and build source/wheel and example Skill archives. CI does not need a model
key, private service, robot, camera, or prebuilt Gateway binary.

## Historical operator-run integration evidence

An operator-run benchmark used an externally configured multimodal provider,
MuJoCo RGB-D and a contact-based three-axis parallel gripper. One complete
physical manipulation and three localization seeds were exercised. All 12
cases passed, including correct rejection of unexecuted/incomplete actions.
The model and effort came from deployment configuration; provider-specific
route labels and raw requests/media are not distributed here.

The observed run used an explicit 8192-token output budget and 180000-ms
deadline, totaling 106.923 seconds of model-call wall time. Localization IoUs
were 0.8272, 0.8832 and 0.7746; all selected grasp/destination pixels hit the
intended masks. The target was released inside the tray with zero neighbor
displacement according to independent physics evaluation. No weld attachment
or oracle-coordinate fallback was used.

An actual PAOS/Gateway run separately completed seven calls in 7.804 seconds:
grounding 3.941 s, geometry preparation 0.102 s, accelerated physics execution
0.203 s, review 3.236 s, plus capability/observation calls. Its model/effort
were explicitly checked and task context advanced from version 1 to 2.
This evidence is historical and has not been reproduced by public CI.

An earlier operator run selected a different provider route instead of the
supplied configuration. Those results are not relabeled as configured-model
validation. The configuration reader and demo launcher were corrected in
0.1.1. Failed attempts remain in local operator records.

## Limits and reproduction

These samples are not latency guarantees or grasp success-rate estimates.
The example is a tabletop gantry/gripper baseline, not validation of arbitrary
objects, general robot-arm collision planning, physical robots or sustained
high concurrency. Visual conclusions and independent physical truth remain
separate; schema validity alone is not task success.

Use the [user guide](../README.md) and [developer guide](../DEVELOPING.md) to
run the benchmark with your own provider. Keep raw output in ignored reports,
record the effective model/effort, retain failures and negative controls, and
never substitute privileged simulation coordinates when grounding fails.
