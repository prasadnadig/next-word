# lm-serve-platform

This chart owns Envoy software rollout and the edge service exposure contract.

## Owned resources

- Envoy Deployment
- Envoy Service
- Envoy base config
- Envoy auth wiring to the auth chart

## Contract

Model route data should arrive via a named ConfigMap contract owned by the model rollout side.

## Auth Mode Quickstart

Use this chart to connect Envoy to auth service in one of three ways.

### 1) Same namespace (internal)

Use when auth service and Envoy run in the same namespace.

```yaml
authz:
	mode: internal
	endpoint:
		host: lm-serve-auth-service
		namespace: lm-serve
		port: 8080
		scheme: http
```

Behavior impact:
- Envoy resolves auth endpoint through cluster DNS as `host.namespace.svc.cluster.local`.
- No external bearer token secret is needed.

### 2) Different namespace, same cluster (internal)

Use when auth service is shared from another namespace in the same cluster.

```yaml
authz:
	mode: internal
	endpoint:
		host: lm-serve-auth-service
		namespace: security-services
		port: 8080
		scheme: http
```

Behavior impact:
- Envoy still uses Kubernetes DNS.
- Only namespace changes; auth remains in-cluster.

### 3) Different cluster or external endpoint (external)

Use when auth endpoint is outside this cluster.

```yaml
authz:
	mode: external
	endpoint:
		host: authz.company.example
		namespace: ""
		port: 443
		scheme: https
```

Behavior impact:
- Envoy uses `host` verbatim, not Kubernetes service DNS.
- With `scheme: https`, Envoy enables TLS for auth upstream.
- External mode injects `Authorization: Bearer <token>` to auth endpoint calls.

## External Bearer Token Secret Quickstart

The external token secret supports a mutually exclusive choice.

### Option A: chart creates a Secret

Use for local development or quick bootstrap.

```yaml
authz:
	mode: external
	external:
		bearerTokenSecret:
			create: true
			name: lm-serve-authz-bearer-token
			existingSecretName: ""
			key: bearerToken
			value: REPLACE_ME_EXTERNAL_AUTH_BEARER_TOKEN
```

### Option B: use an existing Secret

Use for production and external secret-manager workflows.

```yaml
authz:
	mode: external
	external:
		bearerTokenSecret:
			create: false
			name: lm-serve-authz-bearer-token
			existingSecretName: prod-authz-token
			key: bearerToken
			value: ""
```

Validation rules:
- `create: true` with non-empty `existingSecretName` fails template rendering.
- `create: false` with empty `existingSecretName` fails template rendering.

## Live Route Aggregation Quickstart

Use this mode when model routes are produced independently (possibly in multiple namespaces) and should flow into Envoy without manual chart re-sequencing.

```yaml
routeConfig:
	liveUpdate:
		enabled: true
		pollIntervalSec: 30
		namespaceAllowRegex: ^lm-serve-.*$
		sourceConfigMapNames:
			- lm-serve-envoy-routes
		labelKey: lm-serve/model-route-source
		labelValue: "true"
		sourceKey: routes.json
		requireAnySource: false
```

Behavior impact:
- Platform deploys a route-aggregator controller.
- Controller scans namespaces matching `namespaceAllowRegex`, reads only configured ConfigMap names, filters by label key/value, merges routes, updates Envoy config ConfigMap, and patches Envoy Deployment annotation to trigger rolling restart on route changes.
- No runtime RBAC grant is needed from models chart; platform owns read RBAC via ClusterRole and write/patch RBAC in its own namespace.
- Controller code is delivered as a standalone container image (no embedded script ConfigMap).

Operational notes:
- Model route ConfigMaps must be labeled `lm-serve/model-route-source=true`.
- `sourceKey` defaults to `routes.json`.
- Duplicate route prefixes or conflicting cluster endpoints are rejected by controller merge logic.
- Build/push the controller image from `lm-serve/route-aggregator/` and set `routeConfig.liveUpdate.controller.image.repository` and `tag`.
