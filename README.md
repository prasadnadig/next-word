# next-word

Workspace for the LM Serve split-chart migration and related GPU bootstrap notes.

## Top-level layout

- [lm-serve/](lm-serve/) - Model-serving workspace with charts, scripts, and deployment docs.
- [gpu-operator/](gpu-operator/) - GPU Operator bootstrap notes and setup plan.

## LM Serve entry points

- [lm-serve/Makefile](lm-serve/Makefile) - primary workflow for rendering, installing, reconciling, and smoke testing.
- [lm-serve/README.install.md](lm-serve/README.install.md) - installation and operations guide.
- [lm-serve/README.inference-usage.md](lm-serve/README.inference-usage.md) - consumer usage guide.
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

- [Platform auth mode quickstart](lm-serve/platform/helm/README.md#auth-mode-quickstart)
- [Platform external token secret quickstart](lm-serve/platform/helm/README.md#external-bearer-token-secret-quickstart)
- [Auth chart secret mode quickstart](lm-serve/auth/helm/README.md#secret-mode-quickstart)
- [Models chart reconciler mode quickstart](lm-serve/models/helm/README.md#reconciler-mode-quickstart)
- [Models chart service exposure quickstart](lm-serve/models/helm/README.md#model-service-exposure-quickstart)
- [Publisher chart multi-option quickstart](lm-serve/publisher/helm/README.md#quickstart-multi-option-settings)
- [Decomposition proposal decision matrix](lm-serve/README.chart-decomposition-proposal.md#contract-1-authz-endpoint)

## Component Layout

- [lm-serve/auth/README.md](lm-serve/auth/README.md) - auth component overview and docs index.
- [lm-serve/platform/README.md](lm-serve/platform/README.md) - platform component overview and docs index.
- [lm-serve/models/README.md](lm-serve/models/README.md) - models component overview and docs index.
- [lm-serve/publisher/README.md](lm-serve/publisher/README.md) - publisher component overview and docs index.

## Notes

- Environment-specific model profiles live under `lm-serve/models/helm/`.

## Image registry default and when to override it

The chart defaults intentionally use a local registry value of `localhost` for image names, for example:

```yaml
publisher:
  image:
    registry: localhost
    repository: lm-serve-model-publisher
    tag: "0.1.0"
```

This means the resolved image name is effectively `localhost/lm-serve-model-publisher:0.1.0` unless you override the registry. This is the right default for local development workflows where all container images are built and loaded directly on the same machine or cluster node, such as a local Docker or kind environment.

Use the local registry default when:

- you are building images locally and pushing them to a local daemon or local registry
- you are testing charts without a shared external registry
- the cluster can pull images from the local node or local registry namespace

Replace the registry before production or shared-cluster use when:

- the images are hosted in Docker Hub, GHCR, ECR, ACR, GCR, or another shared registry
- multiple machines or environments need to pull the same image from a centralized registry
- the target cluster cannot resolve or pull from `localhost`

The general rule is: if images are built and stored locally, `localhost` is convenient; if they are built in CI or shared infrastructure and must be pulled by other machines, set the registry to the real registry host and keep the repository name the same. For example, replace `localhost` with `ghcr.io/your-org` or `registry.example.com/team` as appropriate before deployment.

Do not leave a trailing slash in the registry value. Use `localhost`, `ghcr.io`, or `registry.example.com` without a final `/`; the templates join the registry and repository with a single slash.
