# Auth Component

This component provides request authentication for LM Serve. It includes:

- a deployable auth service image that validates API keys/JWTs
- a Helm chart that wires the auth service into cluster runtime with secrets

Use the image docs when you want to run auth outside Helm (custom platform or different orchestrator). Use the Helm docs when you want chart-managed deployment and values-driven configuration.

## What This Component Does

- Exposes an authorization endpoint used by edge policy checks.
- Loads API key and JWT material from secret-backed files.
- Supports independent image lifecycle and chart lifecycle.

## Documentation Map

- [Helm chart README](helm/README.md)
- [Image/runtime README](image/README.md)
