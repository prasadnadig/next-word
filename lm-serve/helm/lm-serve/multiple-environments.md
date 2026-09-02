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
Always layer these three files:
- values-base.yaml
- values.<env>.yaml
- values.<env>.models.yaml

4. Resource governance
Apply quotas and limits per namespace so one environment does not starve others.

5. Secret separation
Keep object storage and auth secrets per environment and namespace.

## Example workflow

Create a new environment profile from sample files:

```bash
cp values.sample-env.yaml values.dev-west.yaml
cp values.sample-env.models.yaml values.dev-west.models.yaml
```

Render for that environment:

```bash
helm template lm-serve-dev-west . -n lm-serve-dev-west \
  -f values-base.yaml \
  -f values.dev-west.yaml \
  -f values.dev-west.models.yaml
```

Deploy with Make:

```bash
make helm-upgrade-base ENV=dev-west NAMESPACE=lm-serve-dev-west HELM_RELEASE=lm-serve-dev-west
make apply-model-reconciler ENV=dev-west NAMESPACE=lm-serve-dev-west HELM_RELEASE=lm-serve-dev-west DEPLOYER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make apply-model-publisher-rbac ENV=dev-west NAMESPACE=lm-serve-dev-west HELM_RELEASE=lm-serve-dev-west
make apply-model-publisher-cronjob ENV=dev-west NAMESPACE=lm-serve-dev-west HELM_RELEASE=lm-serve-dev-west PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

## Practical guidance

1. Start with multi-environment on one cluster for dev and staging.
2. Keep production on a separate cluster when uptime and isolation are critical.
3. Track per-environment GPU usage and reconcile intervals so one lane does not overload the cluster.
