# Multiple Environments On One Cluster

This guide explains when and why to run multiple lm-serve environments on the same Kubernetes cluster, and how to do it safely.

## When this is useful

1. Safe promotion path
Use separate dev, staging, and prod-like environments on one cluster to validate chart changes, model catalog changes, and image upgrades before wider rollout.

2. Team isolation
Different teams can run independent model catalogs, schedules, and rollout cadence without blocking each other.

3. GPU cost efficiency
If GPU nodes are scarce or expensive, a shared cluster with namespace and release isolation is often more cost-effective than many clusters.

4. A/B testing
Run parallel environment profiles with different serving settings such as replicas, tensor parallel size, model dtype, or scheduling policy.

5. Operational rehearsal
Practice upgrades and rollback steps in one environment while others stay stable.

## When to prefer separate clusters

1. Strict blast-radius requirements
If one environment must never impact another, use separate clusters.

2. Compliance boundaries
If controls require physical or account-level separation, isolate by cluster.

3. Heavy noisy-neighbor risk
If model workloads contend for GPU, storage I/O, or memory in unacceptable ways, split clusters.

## Required isolation controls

1. Unique namespace per environment
Example:
- lm-serve-dev-west
- lm-serve-staging
- lm-serve-prod

2. Unique Helm release per environment
Example:
- lm-serve-dev-west
- lm-serve-staging

3. Per-environment values files
The model chart retains consumer-side env files:
- helm/lm-serve-models/values.yaml
- helm/lm-serve-models/values.<env>.yaml

The source catalog definitions live under the publisher chart instead:
- helm/lm-serve-publisher/catalogs/<env>.yaml

This keeps source-of-truth catalog data in the publisher while the model chart stays focused on runtime consumption.

4. Resource governance
Apply quotas and limits per namespace so one environment does not starve others.

5. Secret separation
Keep object storage and auth secrets per environment and namespace.

## Example workflow

Create a new environment profile from chart-local sample files:

```bash
cp helm/lm-serve-models/values.sample-env.yaml helm/lm-serve-models/values.dev-west.yaml
cp helm/lm-serve-publisher/catalogs/sample-env.yaml helm/lm-serve-publisher/catalogs/dev-west.yaml
```

Render for that environment:

```bash
helm template lm-serve-models helm/lm-serve-models -n lm-serve-dev-west \
  -f helm/lm-serve-models/values.yaml \
  -f helm/lm-serve-models/values.dev-west.yaml
```

Deploy with Make:

```bash
make apply-base ENV=dev-west NAMESPACE=lm-serve-dev-west
make apply-model-reconciler ENV=dev-west NAMESPACE=lm-serve-dev-west MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
helm upgrade --install lm-serve-publisher helm/lm-serve-publisher -n lm-publisher --create-namespace \
  -f helm/lm-serve-publisher/values.yaml
```

For the publisher chart specifically, create or copy chart-local environment files in
`helm/lm-serve-publisher/` and manage publisher rollout independently from model rollout. The publisher install is namespace-scoped and does not need `ENV` because it reads the canonical catalog registry from the chart values and catalog files.

## Practical guidance

1. Start with multi-environment on one cluster for dev and staging.
2. Keep production on a separate cluster when uptime and isolation are critical.
3. Track per-environment GPU usage and reconcile intervals so one lane does not overload the cluster.
