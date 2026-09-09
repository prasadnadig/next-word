# Multiple Tenants On One Cluster

This guide focuses on how to run multiple lm-serve tenants safely on a single Kubernetes cluster.

## What "multiple tenants" means here

A tenant is a deployment lane with independent:
- model runtime overrides (`models/helm/values.<tenant>.yaml`)
- source catalog (`publisher/helm/catalogs/<tenant>.yaml`)
- rollout cadence (apply/reconcile schedule)
- optional namespace and auth boundary

## Why run multiple tenants on one cluster

- Promotion lanes: dev -> staging -> prod-like validation.
- Team isolation: independent model and rollout ownership.
- Cost efficiency: shared GPU pool with namespace governance.
- A/B runtime tuning: compare serving config across tenants.

## When to split clusters instead

- strict blast-radius requirements
- hard compliance/account isolation requirements
- heavy noisy-neighbor GPU contention

## Isolation Controls You Must Keep

1. Namespace isolation
- Use one namespace per tenant for runtime workloads.

2. Values isolation
- Keep a dedicated model values overlay per tenant.

3. Catalog isolation
- Keep one source catalog file per tenant.

4. Secret isolation
- Keep object storage pull secrets owned by each tenant models/reconciler release.
- Keep auth secrets separated by your tenant or shared-auth strategy.

5. Resource governance
- Use quotas/limits to prevent one tenant from starving others.

## Multi-Tenant Topology

```mermaid
flowchart TB
  subgraph Publisher[lm-serve-publisher Namespace]
    PV[Publisher CronJob]
    REG[consumer.catalogs registry\nvalues.yaml]
  end

  OBJ[(S3-Compatible Object Storage\npublished-catalogs/<tenant>/models.yaml)]

  subgraph T1[Tenant dev-west Namespace]
    R1[Model Reconciler]
    M1[Model StatefulSets/Services]
  end

  subgraph T2[Tenant staging Namespace]
    R2[Model Reconciler]
    M2[Model StatefulSets/Services]
  end

  PV --> OBJ
  OBJ --> R1 --> M1
  OBJ --> R2 --> M2
```

## Tenant Creation Workflow

```mermaid
sequenceDiagram
  participant Op as Operator
  participant FS as Repo Files
  participant Mk as Makefile
  participant K8s as Kubernetes

  Op->>FS: Copy values.sample-env.yaml -> values.dev-west.yaml
  Op->>FS: Copy catalogs/sample-env.yaml -> catalogs/dev-west.yaml
  Op->>FS: Add tenant entry in publisher values.yaml

  Op->>Mk: make apply-base TENANT=dev-west NAMESPACE=lm-serve-dev-west
  Mk->>K8s: Install auth/platform/models baseline

  Op->>Mk: make apply-model-reconciler TENANT=dev-west NAMESPACE=lm-serve-dev-west
  Mk->>K8s: Enable tenant reconciler job

  Op->>Mk: make apply-serve-model TENANT=dev-west NAMESPACE=lm-serve-dev-west
  Mk->>K8s: One-shot reconcile and wait
```

## Step-by-Step: Add a New Tenant

1. Create tenant files.

```bash
cp models/helm/values.sample-env.yaml models/helm/values.dev-west.yaml
cp publisher/helm/catalogs/sample-env.yaml publisher/helm/catalogs/dev-west.yaml
```

2. Register the tenant in `publisher/helm/values.yaml` under `consumer.catalogs`.

Example shape:

```yaml
consumer:
  catalogs:
    - tenant: dev-west
      enabled: true
      file: dev-west.yaml
```

3. Render and validate.

```bash
make render-helm-template TENANT=dev-west
```

4. Install baseline for that tenant.

```bash
make apply-base TENANT=dev-west NAMESPACE=lm-serve-dev-west
```

5. Reconcile model runtime.

```bash
make apply-model-reconciler TENANT=dev-west NAMESPACE=lm-serve-dev-west MODEL_RECONCILER_IMAGE_REGISTRY=<registry> MODEL_RECONCILER_IMAGE_REPOSITORY=vllm-catalog-deployer MODEL_RECONCILER_IMAGE_TAG=0.1.0
make apply-serve-model TENANT=dev-west NAMESPACE=lm-serve-dev-west MODEL_RECONCILER_IMAGE_REGISTRY=<registry> MODEL_RECONCILER_IMAGE_REPOSITORY=vllm-catalog-deployer MODEL_RECONCILER_IMAGE_TAG=0.1.0
```

6. Populate object-storage pull secret credentials for that tenant models release.

- Models chart creates two placeholder pull secrets (key A + key B).
- Reconciler does not apply workloads until at least one pull secret has non-placeholder credentials.
- Reconciler tries one key and automatically falls back to the other on auth errors.

## Auth Strategy Across Tenants

Per-tenant auth:
- Set `AUTH_NAMESPACE` equal to each tenant namespace.

Shared auth:
- Reuse one `AUTH_NAMESPACE` for multiple tenants.

Examples:

```bash
make apply-base TENANT=dev-west NAMESPACE=lm-serve-dev-west AUTH_NAMESPACE=lm-serve-auth-shared
make apply-base TENANT=staging NAMESPACE=lm-serve-staging AUTH_NAMESPACE=lm-serve-auth-shared
```

## Recurrent Operations

- Enable/disable a tenant publication lane: flip `consumer.catalogs[*].enabled`.
- Roll one tenant only: run `apply-base` and `apply-model-reconciler` with that `TENANT`.
- Change runtime behavior for one tenant: edit only that tenant's `values.<tenant>.yaml`.

## Practical Guardrails

- Keep tenant names DNS-safe (`[a-z0-9-]`) since they influence route segments.
- Avoid sharing model runtime namespaces between tenants.
- Track GPU usage and reconcile intervals per tenant.
- Keep publisher namespace dedicated and stable.
