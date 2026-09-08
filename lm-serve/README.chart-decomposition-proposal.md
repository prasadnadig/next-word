# LM Serve Chart Decomposition Proposal

This document defines a 4-chart split so auth, platform software, model rollout, and model publishing can be released independently.

## Goals

- Separate release cadence for auth, platform software, and models.
- Keep runtime route behavior deterministic when models change.
- Remove auth and Envoy software manifests from runtime script generation.
- Keep lm-serve able to call either an internal or external auth service.

## Proposed Charts

1. `lm-serve-auth`
- Owns auth service runtime and optional secret integration.
- Exposes a stable authz endpoint contract consumed by platform chart.

2. `lm-serve-platform`
- Owns Envoy software rollout and edge service exposure.
- Consumes authz endpoint settings from values.
- Reads route config from a named ConfigMap contract.

3. `lm-serve-models`
- Consumes the published model catalog and owns model runtime rollout (StatefulSets and Services), or owns the model reconciler controller that does so.
- Owns dynamic route artifact data written to the shared route ConfigMap contract.

4. `lm-serve-publisher`
- Owns the source model catalog definitions, publication runtime, RBAC, and CronJob rollout.
- Publishes artifacts into the model storage contract consumed by `lm-serve-models`.

## Ownership Matrix

| Resource | Owner Chart | Notes |
|---|---|---|
| Auth Deployment/Service/ConfigMap | `lm-serve-auth` | Internal auth service implementation |
| Auth Secrets or ExternalSecret refs | `lm-serve-auth` | Use external secret manager in production |
| Envoy Deployment | `lm-serve-platform` | Software rollout lifecycle |
| Envoy Service (`vllm-edge`) | `lm-serve-platform` | Exposure policy (ClusterIP/NodePort/LoadBalancer) |
| Envoy base config | `lm-serve-platform` | Listener, ext_authz, health routes |
| Envoy route config ConfigMap data | `lm-serve-models` | Model-specific routes/clusters |
| Source catalog definitions | `lm-serve-publisher` | Source of model intent for each tenant |
| Published tenant catalog object (S3 path) | `lm-serve-publisher` | Generated artifact consumed by runtime |
| Model StatefulSets/Services | `lm-serve-models` | Direct templates or reconciler-managed |
| Model reconciler Job + RBAC | `lm-serve-models` | If using controller pattern |
| Model publisher CronJob + RBAC | `lm-serve-publisher` | Optional per release |
| Publisher image/config/secret refs | `lm-serve-publisher` | Publisher rollout and runtime settings |

## Cross-Chart Contracts

### Contract 1: Authz endpoint
`lm-serve-auth` publishes endpoint details consumed by `lm-serve-platform`.

Suggested values consumed by `lm-serve-platform`:

```yaml
authz:
  mode: internal # internal | external
  endpoint:
    host: lm-serve-auth-service
    namespace: lm-serve
    port: 8080
    pathPrefix: /authorize
    timeout: 3s
  allowedHeaders:
    - authorization
    - x-api-key
```

Rules:
- If `mode=internal`, endpoint host should default to the auth chart service.
- If `mode=external`, endpoint host should be an externally reachable DNS name or load balancer address, and the platform chart should use that host verbatim instead of Kubernetes service DNS.
- If `mode=external`, the token Secret may be created by the chart for local/testing use or supplied by the user as an existing Secret via `existingSecretName`.

Decision matrix:

| Need | authz.mode | endpoint.host | endpoint.namespace | endpoint.scheme | bearerTokenSecret choice |
|---|---|---|---|---|---|
| Auth in same namespace | `internal` | K8s Service name | workload namespace | `http` | not used |
| Auth in different namespace, same cluster | `internal` | K8s Service name | auth namespace | `http` | not used |
| Auth in different cluster | `external` | routable DNS/LB host | empty string | `https` (recommended) | create Secret or use existing Secret |

Mutual exclusivity for external bearer token secret:
- Option A: `create=true` and `existingSecretName=""`
- Option B: `create=false` and `existingSecretName="<precreated-secret>"`

### Contract 2: Envoy route config source
`lm-serve-platform` mounts a named ConfigMap; `lm-serve-models` writes route data to it.

Suggested contract:

```yaml
routeConfig:
  configMapName: lm-serve-envoy-routes
  key: routes.yaml
  owner: models # fixed convention
```

Rules:
- Platform chart never overwrites model-generated route data.
- Models chart/reconciler writes only route/clusters payload.
- Envoy restart behavior is explicit (hash annotation update or reloader integration).

### Contract 3: Release ordering
- `lm-serve-auth` and `lm-serve-platform` can be upgraded independently.
- `lm-serve-models` upgrades may require route config refresh + Envoy reload trigger.
- `lm-serve-publisher` upgrades are independent and do not require Envoy reload.

## Suggested Values Schemas

## `lm-serve-auth` values

```yaml
authService:
  enabled: true
  name: lm-serve-auth-service
  image:
    repository: python
    tag: 3.12.12-slim-bookworm
    pullPolicy: IfNotPresent
  replicaCount: 2
  service:
    port: 8080
  resources:
    requests:
      cpu: 50m
      memory: 64Mi
    limits:
      cpu: 250m
      memory: 256Mi

secrets:
  mode: externalRef # externalRef | existingSecret | create
  existingSecretName: lm-serve-auth-secrets
  keys:
    apiKeys: api_keys.txt
    jwtSecrets: jwt_hs256_secrets.txt
  externalRef:
    enabled: false
    # provider-specific fields in tenant overlays
```

## `lm-serve-platform` values

```yaml
envoy:
  enabled: true
  name: vllm-envoy
  image:
    repository: envoyproxy/envoy
    tag: v1.33.2
    pullPolicy: IfNotPresent
  replicaCount: 2
  service:
    name: vllm-edge
    type: LoadBalancer
    port: 80
    targetPort: 8080
  admin:
    port: 9901
  health:
    path: /healthz
  resources:
    requests:
      cpu: 200m
      memory: 256Mi
    limits:
      cpu: 1000m
      memory: 1Gi

authz:
  mode: internal
  endpoint:
    host: lm-serve-auth-service
    namespace: lm-serve
    port: 8080
    pathPrefix: /authorize
    timeout: 3s
  allowedHeaders:
    - authorization
    - x-api-key

routeConfig:
  configMapName: lm-serve-envoy-routes
  key: routes.yaml
  reloadStrategy: rollout # rollout | reloader
```

## `lm-serve-models` values

```yaml
catalog:
  enabled: false
  name: lm-serve-model-catalog
  key: models.yaml
  tenant: sample-env
  publishedPrefix: published-catalogs
  storage:
    bucket: REPLACE_ME_BUCKET
    endpoint: https://us-ord-1.linodeobjects.com
    region: us-east-1
  models: []

modelReconciler:
  enabled: true
  image: REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
  args:
    catalogKey: models.yaml
    routeConfigMapName: lm-serve-envoy-routes
    routeConfigKey: routes.yaml
    watchIntervalSec: "60"

modelPublisher:
  enabled: false
  image: REPLACE_ME_REGISTRY/lm-serve-model-publisher:0.1.0

routeConfig:
  configMapName: lm-serve-envoy-routes
  key: routes.yaml

## `lm-serve-publisher` values

```yaml
publisher:
  enabled: true
  image: REPLACE_ME_REGISTRY/lm-serve-model-publisher:0.1.0
  imagePullPolicy: IfNotPresent
  schedule: "0 */6 * * *"
  serviceAccountName: lm-serve-model-publisher
  pruneStaging: true

rbac:
  enabled: true
  s3SecretName: lm-publisher-object-storage-creds
  hfSecretName: lm-publisher-hf-credentials
```
```

## Script Implications

For `deploy_lm_serve_catalog.py`, target end state:

- Keep:
  - model StatefulSet/Service reconciliation logic.
  - route payload generation logic.
- Remove:
  - auth service manifest generation and apply.
  - Envoy Deployment/Service manifest generation and apply.
- Add:
  - route-config-only apply path for shared ConfigMap contract.
  - optional Envoy restart trigger hook if rollout strategy requires it.

## Migration Plan

1. Phase 1: Auth chart extraction
- Create `lm-serve-auth` chart and deploy it.
- Update platform/reconciler to read auth endpoint from values.
- Stop auth manifest creation in script after parity validation.

2. Phase 2: Platform chart extraction
- Move Envoy Deployment/Service to `lm-serve-platform`.
- Keep dynamic routes generated by models/reconciler.
- Introduce shared route ConfigMap contract and reload strategy.

3. Phase 3: Publisher chart isolation
- Move model publisher RBAC + CronJob into `lm-serve-publisher`.
- Keep publisher release cadence independent from model rollout.

4. Phase 4: Models chart isolation
- Move model reconciler + catalog + model publisher into `lm-serve-models`.
- Optionally keep direct StatefulSet templating out of Helm and use reconciler only.

5. Phase 5: Cleanup
- Remove old mixed ownership toggles from current chart.
- Update Make targets to map cleanly to four chart releases.

## Make Target Direction (post-split)

Suggested command groups:

- Auth chart:
  - `apply-auth-base`
  - `upgrade-auth`

- Platform chart:
  - `render-platform-template`
  - `upgrade-platform`

- Publisher chart:
  - `render-publisher-template`
  - `upgrade-publisher`
  - `apply-publisher-cronjob`

- Models chart:
  - `render-models-template`
  - `upgrade-models`
  - `reconcile-models-once`
  - `reconcile-models-watch`

This keeps software rollout and model rollout independently versioned while preserving model-driven routing behavior.
