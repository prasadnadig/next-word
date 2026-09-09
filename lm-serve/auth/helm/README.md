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
The chart builds file-based auth credentials by combining all enabled `secrets.catalogs` entries.

```yaml
secrets:
	mode: create
	existingSecretName: lm-serve-auth-secrets
	keys:
		apiKeys: api_keys.txt
		jwtSecrets: jwt_hs256_secrets.txt
	catalogs:
		- tenant: sample-env
			enabled: true
			apiKeys:
				- team-dev-key-1
			jwtSecrets:
				- replace-with-strong-hs256-secret
```

Behavior impact:
- Chart renders a Kubernetes Secret.
- Auth service reads API keys and JWT secrets from mounted files.

### 2) `mode: existingSecret`

Use when secret data is provisioned outside this chart.

```yaml
secrets:
	mode: existingSecret
	existingSecretName: prod-auth-secrets
	keys:
		apiKeys: api_keys.txt
		jwtSecrets: jwt_hs256_secrets.txt
```

Behavior impact:
- Chart does not create a Secret.
- Deployment references an existing Secret by name.

## Canonical values registry

The base [values.yaml](values.yaml) is now the canonical multi-tenant registry for auth credentials:

- Add or disable entries under `secrets.catalogs`.
- Each enabled tenant contributes keys/secrets to the generated secret files.
- This chart no longer requires tenant-specific values overlays for Make-based rendering.

The auth chart can still be overridden with extra files or `--set`, but `TENANT` is not required for auth chart rendering.

## Delivery workflow

The auth service source code and Docker build context live in [../image/](../image/).

Build and push with Make from [lm-serve/](../../):

```bash
make build-auth-image AUTH_IMAGE_REGISTRY=<registry> AUTH_IMAGE_REPOSITORY=lm-serve-auth-service AUTH_IMAGE_TAG=0.1.0
make push-auth-image AUTH_IMAGE_REGISTRY=<registry> AUTH_IMAGE_REPOSITORY=lm-serve-auth-service AUTH_IMAGE_TAG=0.1.0
make apply-auth-service AUTH_IMAGE_REGISTRY=<registry> AUTH_IMAGE_REPOSITORY=lm-serve-auth-service AUTH_IMAGE_TAG=0.1.0
```
