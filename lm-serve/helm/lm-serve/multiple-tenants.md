# Multiple Tenants On One Cluster

This guide explains when and why to run multiple lm-serve tenants on the same Kubernetes cluster, and how to do it safely.

## When this is useful

1. Safe promotion path
Use separate dev, staging, and prod-like tenants on one cluster to validate chart changes, model catalog changes, and image upgrades before wider rollout.

2. Team isolation
Different teams can run independent model catalogs, schedules, and rollout cadence without blocking each other.

3. GPU cost efficiency
If GPU nodes are scarce or expensive, a shared cluster with namespace and release isolation is often more cost-effective than many clusters.

4. A/B testing
Run parallel tenant profiles with different serving settings such as replicas, tensor parallel size, model dtype, or scheduling policy.

5. Operational rehearsal
Practice upgrades and rollback steps in one tenant while others stay stable.

## When to prefer separate clusters

1. Strict blast-radius requirements
If one tenant must never impact another, use separate clusters.

2. Compliance boundaries
If controls require physical or account-level separation, isolate by cluster.

3. Heavy noisy-neighbor risk
If model workloads contend for GPU, storage I/O, or memory in unacceptable ways, split clusters.

## Required isolation controls

1. Unique namespace per tenant
Example:
- lm-serve-dev-west
- lm-serve-staging
- lm-serve-prod

2. Unique Helm release per tenant
Example:
- lm-serve-dev-west
- lm-serve-staging

3. Per-tenant values files
Always layer the consumer-side model chart files:
- helm/lm-serve-models/values.yaml
- helm/lm-serve-models/values.<tenant>.yaml

The source catalog definitions live under the publisher chart instead:
- helm/lm-serve-publisher/catalogs/<tenant>.yaml

4. Resource governance
Apply quotas and limits per namespace so one tenant does not starve others.

5. Secret separation
Keep object storage and auth secrets per tenant and namespace.

6. Auth namespace strategy
- Per-tenant auth: set `AUTH_NAMESPACE` equal to each tenant namespace.
- Shared auth: set one common `AUTH_NAMESPACE` across all tenants.

## Example workflow

Create a new tenant profile from chart-local sample files:

```bash
cp ../helm/lm-serve-models/values.sample-env.yaml ../helm/lm-serve-models/values.dev-west.yaml
cp ../helm/lm-serve-publisher/catalogs/sample-env.yaml ../helm/lm-serve-publisher/catalogs/dev-west.yaml
```

Render for that tenant:

```bash
helm template lm-serve-models ../helm/lm-serve-models -n lm-serve-dev-west \
  -f ../helm/lm-serve-models/values.yaml \
  -f ../helm/lm-serve-models/values.dev-west.yaml
```

Deploy with Make:

```bash
make apply-base TENANT=dev-west NAMESPACE=lm-serve-dev-west AUTH_NAMESPACE=lm-serve-dev-west
make apply-model-reconciler TENANT=dev-west NAMESPACE=lm-serve-dev-west MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
helm upgrade --install lm-serve-publisher ../helm/lm-serve-publisher -n lm-serve-dev-west --create-namespace \
  -f ../helm/lm-serve-publisher/values.yaml
```

Shared-auth example:

```bash
make apply-base TENANT=dev-west NAMESPACE=lm-serve-dev-west AUTH_NAMESPACE=lm-serve-auth-shared
make apply-base TENANT=staging NAMESPACE=lm-serve-staging AUTH_NAMESPACE=lm-serve-auth-shared
```

For the publisher chart specifically, create or copy chart-local tenant files in
`../helm/lm-serve-publisher/` and manage publisher rollout independently from model rollout.

## Practical guidance

1. Start with multi-tenant on one cluster for dev and staging.
2. Keep production on a separate cluster when uptime and isolation are critical.
3. Track per-tenant GPU usage and reconcile intervals so one lane does not overload the cluster.
