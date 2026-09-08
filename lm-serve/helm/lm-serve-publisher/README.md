# lm-serve-publisher

This chart owns the source model catalogs, the publisher CronJob, and publisher RBAC for catalog publication.

## Ownership boundary

The source catalog definitions live in [catalogs](catalogs/). Those files are the source of truth for what models are available in each tenant.

- `catalogs/sample-env.yaml` - sample tenant catalog
- `catalogs/small-representative.yaml` - small representative tenant catalog
- `catalogs/tiny-smoke.yaml` - smoke-test tenant catalog

These catalog definitions are published to object storage as tenant catalog files. The model-runtime chart consumes those published catalog artifacts; it does not define the input catalog source.

## Owned resources

- Source catalog definitions for each tenant
- Publisher ServiceAccount
- Publisher Role and RoleBinding
- Publisher CronJob
- Publisher runtime image settings

Not owned by this chart:

- Reconciler object-storage pull secrets
- Reconciler cross-namespace secret RBAC

Those are intentionally owned by the models/reconciler chart to keep publisher and reconciler decoupled.

## Canonical values

The base [values.yaml](values.yaml) is the canonical registry for all publisher tenants. Add new tenant entries directly to `consumer.catalogs` there, and keep the raw catalog YAML files in [catalogs](catalogs/).

Each catalog entry supports an `enabled` flag. The publisher renders and publishes only the entries where `enabled` is true. This lets you disable a catalog by tenant without removing its metadata or source file.

## Quickstart: Multi-option Settings

### 1) Staging cleanup

```yaml
publisher:
	pruneStaging: true # true | false
```

When to use what:
- `true`: default for routine operation; removes stale staged artifacts.
- `false`: keep staged artifacts for debugging or audit workflows.

### 2) Per-tenant publish enablement

```yaml
consumer:
	catalogs:
		- tenant: sample-env
			enabled: true
		- tenant: small-representative
			enabled: false
```

Behavior impact:
- Only enabled catalogs are rendered and passed to publisher runtime.
- Disabled catalogs remain documented but are not published.

### 3) Disable consumer artifact management

```yaml
consumer:
	enabled: false
```

Behavior impact:
- Chart skips consumer artifact scaffolding paths.
- Use only when those artifacts are managed elsewhere.

## Release example

```bash
helm upgrade --install lm-serve-publisher . -n lm-publisher --create-namespace \
	-f values.yaml
```

The publisher install does not require `TENANT`; the registry of tenant catalog entries already lives in `values.yaml`. The chart is intentionally installed in its own dedicated namespace such as `lm-publisher`.

## Contract

This chart should not own model rollout state or Envoy configuration. It publishes the catalog; downstream runtime charts consume it.
