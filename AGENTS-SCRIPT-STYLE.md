# AGENTS-SCRIPT-STYLE.md

## Purpose

Capture repository preference for script delivery and lifecycle management.

## Rules

- Do not embed non-trivial scripts in Kubernetes YAML manifests (for example ConfigMap data blocks).
- If logic is more than a simple one-line command invocation, place it in a standalone source file under a dedicated component directory.
- Package standalone scripts into a dedicated container image and reference that image from Helm/Kubernetes manifests.
- Keep manifests focused on orchestration concerns (env vars, mounts, RBAC, rollout behavior), not implementation code.
- For tiny wrappers that only invoke one command with stable flags, inline shell command fields in manifests are acceptable.

## Implementation Guidance

- Preferred layout for service scripts:
  - `lm-serve/<component>/` with script source, Dockerfile, and README.
- Prefer explicit image repository/tag values in chart defaults so environments can override cleanly.
- Document build and push steps near the script component.
