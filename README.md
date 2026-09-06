# next-word

Workspace for the LM Serve split-chart migration and related GPU bootstrap notes.

## Top-level layout

- [lm-serve/](lm-serve/) - Model-serving workspace with charts, scripts, and deployment docs.
- [gpu-operator/](gpu-operator/) - GPU Operator bootstrap notes and setup plan.

## LM Serve entry points

- [lm-serve/Makefile](lm-serve/Makefile) - primary workflow for rendering, installing, reconciling, and smoke testing.
- [lm-serve/deploy/README-install.md](lm-serve/deploy/README-install.md) - installation and operations guide.
- [lm-serve/deploy/README-inference-usage.md](lm-serve/deploy/README-inference-usage.md) - consumer usage guide.
- [lm-serve/README.chart-decomposition-proposal.md](lm-serve/README.chart-decomposition-proposal.md) - chart split proposal, ownership matrix, and auth mode decision matrix.

## Configuration Modes Cheatsheet

Use this as a quick decision map before picking values.

- Auth endpoint placement:
	- Same namespace: platform auth mode internal with auth service namespace set to workload namespace.
	- Different namespace, same cluster: platform auth mode internal with auth namespace set to shared namespace.
	- Different cluster: platform auth mode external with routable host and HTTPS.
- External auth bearer token source:
	- Chart-managed secret: create the token secret in platform chart values.
	- Existing secret: reference a pre-created secret in platform chart values.
- Auth service credential source:
	- Chart-managed: auth chart secrets mode create.
	- Existing secret: auth chart secrets mode existingSecret.
- Model reconciler operation:
	- In-cluster job: models chart modelReconciler enabled true.
	- External/manual reconcile flow: models chart modelReconciler enabled false.
- Model service exposure:
	- Internal-only: ClusterIP.
	- Node/network exposure: NodePort.
	- Managed external endpoint: LoadBalancer.

Direct chart quickstarts:

- [Platform auth mode quickstart](lm-serve/helm/lm-serve-platform/README.md#auth-mode-quickstart)
- [Platform external token secret quickstart](lm-serve/helm/lm-serve-platform/README.md#external-bearer-token-secret-quickstart)
- [Auth chart secret mode quickstart](lm-serve/helm/lm-serve-auth/README.md#secret-mode-quickstart)
- [Models chart reconciler mode quickstart](lm-serve/helm/lm-serve-models/README.md#reconciler-mode-quickstart)
- [Models chart service exposure quickstart](lm-serve/helm/lm-serve-models/README.md#model-service-exposure-quickstart)
- [Publisher chart multi-option quickstart](lm-serve/helm/lm-serve-publisher/README.md#quickstart-multi-option-settings)
- [Decomposition proposal decision matrix](lm-serve/README.chart-decomposition-proposal.md#contract-1-authz-endpoint)

## Chart set

- [lm-serve/helm/lm-serve-auth/](lm-serve/helm/lm-serve-auth/) - auth service chart.
- [lm-serve/helm/lm-serve-platform/](lm-serve/helm/lm-serve-platform/) - Envoy platform chart.
- [lm-serve/helm/lm-serve-models/](lm-serve/helm/lm-serve-models/) - model catalog, route contract, and reconciler chart.
- [lm-serve/helm/lm-serve-publisher/](lm-serve/helm/lm-serve-publisher/) - model publisher chart.

## Notes

- The old shared `helm/lm-serve/` chart directory is now archived notes and sample overlays only.
- Environment-specific model profiles live under `lm-serve/helm/lm-serve-models/`.
