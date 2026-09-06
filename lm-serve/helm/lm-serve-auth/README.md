# lm-serve-auth

This chart owns the auth service runtime and the auth secret wiring contract.

## Owned resources

- Auth Deployment
- Auth Service
- Auth container runtime image
- Secret references or external secret integration

## Contract

Consumers should configure the auth endpoint via chart values rather than hardcoding service names.

## Secret Mode Quickstart

This chart supports two credential wiring modes.

### 1) `mode: create`

Use when you want this chart to create auth secret data.

```yaml
secrets:
	mode: create
	existingSecretName: lm-serve-auth-secrets
	keys:
		apiKeys: api_keys.txt
		jwtSecret: jwt_hs256_secret
	apiKeys:
		- team-dev-key-1
	jwtSecret: replace-with-strong-hs256-secret
```

Behavior impact:
- Chart renders a Kubernetes Secret.
- Auth service reads API keys and JWT secret from mounted files.

### 2) `mode: existingSecret`

Use when secret data is provisioned outside this chart.

```yaml
secrets:
	mode: existingSecret
	existingSecretName: prod-auth-secrets
	keys:
		apiKeys: api_keys.txt
		jwtSecret: jwt_hs256_secret
```

Behavior impact:
- Chart does not create a Secret.
- Deployment references an existing Secret by name.

## Sample environment

This chart includes these environment overlays:

- [values.sample-env.yaml](values.sample-env.yaml)
- [values.small-representative.yaml](values.small-representative.yaml)
- [values.tiny-smoke.yaml](values.tiny-smoke.yaml)

Each overlay enables chart-managed secret creation for testing and is selected via `ENV` in the Make workflow.

## Delivery workflow

The auth service source code and Docker build context live in [auth-service/](../../auth-service/).

Build and push with Make from [lm-serve/](../../):

```bash
make build-auth-image AUTH_IMAGE=<registry>/lm-serve-auth-service:0.1.0
make push-auth-image AUTH_IMAGE=<registry>/lm-serve-auth-service:0.1.0
make apply-auth-service AUTH_IMAGE=<registry>/lm-serve-auth-service:0.1.0
```
