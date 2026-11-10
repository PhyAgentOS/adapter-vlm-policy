# Security policy

## Reporting

Use this repository's private vulnerability reporting when available. If it
is unavailable, request a private contact from the organization maintainers
without posting exploit details, credentials, media or personal data.
Do not submit sensitive vulnerability reports in a public issue.

Security fixes target the current maintained source branch.

## Deployment boundary

- The default service listens on loopback. Non-loopback CLI startup requires
  configured Bearer credentials. Protect remote deployments with TLS and
  appropriate network access controls; this service does not terminate TLS.
- Configuration, provider files, allowed image directories and the service
  data directory are operator-controlled inputs. Keep them on a trusted
  filesystem with appropriate permissions. Do not grant a client image-root
  access to credential directories or unrelated private data.
- Namespace checks isolate HTTP clients. A caller allowed to read a Task
  snapshot can read its selected prompts, model outputs and image references.
  Treat stored context, snapshots and media as sensitive application data.
- Prompts/images are sent to the configured model provider. Review that
  provider's data handling before submitting sensitive content. Model output
  is untrusted; schema validity is not a safety or task-success guarantee.
- Gateway and the example simulator are loopback-only test components. Do not
  expose the simulator's offline evaluator as an Agent Tool or a public API.
- The service does not execute model-produced commands. The separate MuJoCo
  example is simulation-only; its primitive controller is not a physical
  robot safety controller. Cancelling inference does not stop a robot.
- Runtime storage uses POSIX file locking and one process per data directory.
  This release does not establish Windows support, distributed locking or
  sustained untrusted multi-tenant operation.
