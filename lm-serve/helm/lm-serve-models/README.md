# lm-serve-models

This chart consumes the published model catalog and owns the model-runtime rollout plus the route artifact contract consumed by Envoy.

## Ownership boundary

The source catalog definitions are owned by the publisher chart under [../lm-serve-publisher/catalogs](../lm-serve-publisher/catalogs/). The models chart consumes the published catalog ConfigMap and turns it into running model resources.

## Owned resources

- Route ConfigMap rendered from catalog data
- Model StatefulSets and Services, or the model reconciler controller that creates them
- Route ConfigMap data for Envoy

## Contract

Keep route data deterministic and independent from platform rollout versioning. The publisher is the source of catalog intent; the models chart is the consumer runtime.

Route ConfigMap output details:

- When at least one model route is enabled, the chart emits route ConfigMap data in both:
	- `routes.yaml` (YAML)
	- `routes.json` (JSON)
- Route ConfigMap is labeled `lm-serve/model-route-source=true` so platform live aggregator can discover it across namespaces.

Route prefix strategy:

- Namespaced-only: routes are generated as `/m/<tenant>/<model>/`.
- `<tenant>` defaults to the Helm release namespace (sanitized to lowercase URL-safe segment).
- Optional overrides are supported through `routeConfig.namespaceTenantSegmentMap`, mapping namespace to alias.

Use namespace alias mapping when you want a stable short tenant token in URLs while still deploying releases in longer namespace names.

## Tenant profiles

This chart includes tenant profiles for consumer-side deployment settings:

- `values.sample-env.yaml`
- `values.small-representative.yaml`
- `values.tiny-smoke.yaml`

The catalog source-of-truth lives under the publisher chart's `catalogs/` directory instead of here.

## Reconciler Mode Quickstart

### 1) In-cluster reconciler enabled

Use when Helm-managed reconciliation is desired.

```yaml
modelReconciler:
	enabled: true
	image: registry.example.com/vllm-catalog-deployer:0.1.0
```

Behavior impact:
- Chart renders reconciliation Job + RBAC.
- Catalog changes can be reconciled by in-cluster workflow.

### 2) Reconciler disabled

Use when reconciliation runs via external automation or manual script.

```yaml
modelReconciler:
	enabled: false
```

Behavior impact:
- Chart does not render reconciliation Job.
- You must run reconciliation through other operational paths.

## Model Service Exposure Quickstart

Configure `modelReconciler.args.modelServiceType` based on access needs.

```yaml
modelReconciler:
	args:
		modelServiceType: ClusterIP # ClusterIP | NodePort | LoadBalancer
```

When to use what:
- `ClusterIP`: internal-only inference traffic.
- `NodePort`: direct node-based access in constrained setups.
- `LoadBalancer`: cloud-managed external entry point for clients.
