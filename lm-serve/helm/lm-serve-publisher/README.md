# lm-serve-publisher

This chart owns the source model catalogs, the publisher CronJob, RBAC, and secret wiring for catalog publication.

## Ownership boundary

The source catalog definitions live in [catalogs](catalogs/). Those files are the source of truth for what models are available in each environment.

- `catalogs/sample-env.yaml` - sample environment catalog
- `catalogs/small-representative.yaml` - small representative environment catalog
- `catalogs/tiny-smoke.yaml` - smoke-test environment catalog

These catalog definitions are published into the namespace where consumers read them. The model-runtime chart consumes the published catalog; it does not define the input catalog source.

## Owned resources

- Source catalog definitions for each environment
- Publisher ServiceAccount
- Publisher Role and RoleBinding
- Publisher CronJob
- Publisher runtime image settings

## Canonical values

The base [values.yaml](values.yaml) is the canonical registry for all publisher environments. Add new env entries directly to `consumer.catalogs` there, and keep the raw catalog YAML files in [catalogs](catalogs/).

Each catalog entry supports an `enabled` flag. The publisher renders and publishes only the entries where `enabled` is true. This lets you disable a catalog by env without removing its metadata or source file.

## Release example

```bash
helm upgrade --install lm-serve-publisher . -n lm-publisher --create-namespace \
	-f values.yaml
```

The publisher install does not require `ENV`; the registry of env catalog entries already lives in `values.yaml`. The chart is intentionally installed in its own dedicated namespace such as `lm-publisher`.

## Contract

This chart should not own model rollout state or Envoy configuration. It publishes the catalog; downstream runtime charts consume it.
