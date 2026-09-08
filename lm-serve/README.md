# LM Serve Module

This module provides a split-chart, tenant-aware baseline for publishing model artifacts and serving them through vLLM + Envoy with auth controls.

The design goal is simple:
- Publisher owns source-of-truth model intent.
- Model runtime consumes published catalogs.
- Platform owns edge routing/auth integration.
- Tenants are isolated through namespace + values overlays.

## Summary

- Use `TENANT=<tenant-name>` for tenant-scoped model workflows.
- Keep publisher installed in its own namespace (commonly `lm-publisher`).
- Keep one model values overlay per tenant: `helm/lm-serve-models/values.<tenant>.yaml`.
- Keep one source catalog per tenant: `helm/lm-serve-publisher/catalogs/<tenant>.yaml`.

## Components

- `Makefile`
  - Primary operator entrypoint for render/apply/reconcile/smoke flows.
- `helm/lm-serve-auth/`
  - Auth service runtime and auth secret wiring.
- `auth-service/`
  - Python auth service source + image build context.
- `helm/lm-serve-platform/`
  - Envoy edge and route-aggregation integration.
- `route-aggregator/`
  - Route aggregation runtime for multi-namespace route updates.
- `helm/lm-serve-models/`
  - Model runtime consumer chart + reconciler job.
- `deploy/`
  - Reconciler and smoke helpers used by Make workflows.
- `helm/lm-serve-publisher/`
  - Source catalogs, publisher CronJob, RBAC, and tenant catalog publication.
- `publisher/`
  - Model artifact publisher source + image build context.

## Architecture

```mermaid
flowchart LR
  A[Catalog Source Files\nhelm/lm-serve-publisher/catalogs/*.yaml] --> B[Publisher CronJob\nhelm/lm-serve-publisher]
  B --> C[(S3-Compatible Object Storage)]
  C --> D[Published Tenant Catalog Files\nper tenant]

  D --> E[Model Reconciler Job\nhelm/lm-serve-models]
  E --> F[Model StatefulSets + Services]
  E --> G[Route ConfigMap\nroutes.yaml + routes.json]

  G --> H[Route Aggregator\nroute-aggregator]
  H --> I[Envoy Edge\nhelm/lm-serve-platform]

  J[Auth Service\nhelm/lm-serve-auth] --> I
  K[Client\nAPI key or JWT] --> I
  I --> F
```

## Deployment Order

```mermaid
sequenceDiagram
  participant Op as Operator
  participant Mk as Makefile
  participant Auth as lm-serve-auth
  participant Plat as lm-serve-platform
  participant Models as lm-serve-models
  participant Pub as lm-serve-publisher
  participant K8s as Kubernetes

  Op->>Mk: make apply-base TENANT=sample-env
  Mk->>Auth: helm upgrade --install
  Mk->>Plat: helm upgrade --install
  Mk->>Models: helm upgrade --install (reconciler disabled)
  Mk->>K8s: apply example secrets

  Op->>Mk: make apply-model-reconciler TENANT=sample-env
  Mk->>Models: enable reconciler job

  Op->>Mk: make apply-serve-model TENANT=sample-env
  Mk->>K8s: run one reconcile pass and wait

  Op->>Mk: make smoke-test-api-key or smoke-test-jwt
```

## Tenant Model

- `TENANT` selects the model chart overlay file `values.<tenant>.yaml`.
- `NAMESPACE` controls where model runtime and platform resources land.
- `AUTH_NAMESPACE` defaults to `TENANT` when set, else `NAMESPACE`.

Auth patterns:
- Per-tenant auth: `AUTH_NAMESPACE=<tenant-namespace>` per tenant.
- Shared auth: one shared `AUTH_NAMESPACE` used by multiple tenants.

## First-Time Happy Path

1. Review available targets.

```bash
make help
```

2. Render all charts for your tenant.

```bash
make render-helm-template TENANT=sample-env
```

3. Install baseline components.

```bash
make apply-base TENANT=sample-env
```

4. Reconcile model runtime from the published catalog.

```bash
make apply-serve-model TENANT=sample-env
```

5. Validate inference path.

```bash
make smoke-test-api-key API_KEY=<api-key>
# or
make smoke-test-jwt JWT_TOKEN=<jwt>
```

6. Full one-command flow (after image settings are ready).

```bash
make happy-apply TENANT=sample-env API_KEY=<api-key>
```

## Recurrent Operator Happy Paths

- Update one tenant runtime settings and apply:

```bash
make apply-base TENANT=dev-west NAMESPACE=lm-serve-dev-west
make apply-model-reconciler TENANT=dev-west NAMESPACE=lm-serve-dev-west MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
```

- Keep continuous reconciliation on:

```bash
make deploy-watch-local TENANT=dev-west NAMESPACE=lm-serve-dev-west MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
```

- Publish artifact catalogs independently:

```bash
make apply-model-publisher PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

## Customization Controls

Core controls:
- `TENANT`: selects `helm/lm-serve-models/values.<tenant>.yaml`.
- `NAMESPACE`: target runtime namespace.
- `AUTH_NAMESPACE`: auth deployment namespace.
- `HELM_EXTRA_ARGS`: additional Helm flags (`-f`, `--set`, etc).

Image controls:
- `AUTH_IMAGE`
- `MODEL_RECONCILER_IMAGE`
- `ROUTE_AGGREGATOR_IMAGE_REPOSITORY`
- `ROUTE_AGGREGATOR_IMAGE_TAG`
- `PUBLISHER_IMAGE`

Reconciler controls:
- `MODEL_RECONCILER_JOB_NAME`
- `MODEL_RECONCILER_WATCH_INTERVAL_SEC`
- `MODEL_SERVICE_TYPE`
- `CATALOG_CONFIGMAP`
- `CATALOG_KEY`

Release controls:
- `FORCE=true`: bypass cluster confirmation prompts.
- `PUBLISH_IF_MISSING=true`: skip image push when registry already has the tag.

## Tenant Artifacts: Source vs Consumer

- Source-of-truth catalogs:
  - `helm/lm-serve-publisher/catalogs/<tenant>.yaml`
- Consumer runtime overlays:
  - `helm/lm-serve-models/values.<tenant>.yaml`

This separation keeps model intent and runtime policy independently evolvable.

## Publisher Notes

- Publisher chart install does not require `TENANT`.
- Tenant selection for publication is managed in `helm/lm-serve-publisher/values.yaml` via `consumer.catalogs[*].tenant` and `enabled`.

## Cloud Portability

Data-plane portability is intentional:
- S3-compatible APIs (endpoint/bucket/region) instead of cloud-specific SDK lock-in.
- Cluster-level portability through chart values and labels.

To move clouds, update:
- storage endpoint/credentials/bucket conventions
- storage classes
- node selectors/tolerations/affinity in tenant overlays

## Related Docs

- `deploy/README-install.md`
- `deploy/README-inference-usage.md`
- `README.multiple-tenants.md`
- `helm/lm-serve-models/README.md`
- `helm/lm-serve-publisher/README.md`
- `helm/lm-serve-auth/README.md`
