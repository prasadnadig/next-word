# lm-serve-publisher

This chart owns the source model catalogs, the publisher CronJob, and publisher RBAC for catalog publication.

## Ownership boundary

The source catalog definitions live in [catalogs](catalogs/). Those files are the source of truth for what models are available in each tenant.

- `catalogs/models.yaml` - global catalog: every known model, defined once (unique `name`, `source`, `storage.prefix`, base `serving` block)
- `catalogs/sample-env.yaml` - sample tenant selection (which global model names are enabled for this tenant)
- `catalogs/small-representative.yaml` - small representative tenant selection
- `catalogs/tiny-smoke.yaml` - smoke-test tenant selection

Tenant selection files only reference models by `name` and do not redefine `source`/`storage`/`serving`; they can optionally set a tenant-local `enabled` flag and a `servingOverrides` block that is deep-merged onto the global `serving` block for that tenant only. A model publishes for a tenant only if it is enabled in both `catalogs/models.yaml` and that tenant's selection file.

These catalog definitions are published to object storage as tenant catalog files. The model-runtime chart consumes those published catalog artifacts; it does not define the input catalog source.

## Owned resources

- Global model catalog definition and its ConfigMap
- Per-tenant catalog selection definitions and ConfigMaps
- Publisher ServiceAccount
- Publisher Role and RoleBinding
- Publisher CronJob
- Publisher runtime image settings

Not owned by this chart:

- Reconciler object-storage pull secrets

Those are intentionally owned by the models/reconciler chart to keep publisher and reconciler decoupled.

## Canonical values

The base [values.yaml](values.yaml) is the canonical registry for all publisher tenants. Add new models to `catalogs/models.yaml`, add new tenant entries directly to `consumer.catalogs`, and keep the tenant selection YAML files in [catalogs](catalogs/).

Each catalog entry supports an `enabled` flag. The publisher renders and publishes only the entries where `enabled` is true. This lets you disable a catalog by tenant without removing its metadata or source file.

## Quickstart: Multi-option Settings

### 1) Per-tenant publish enablement

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

### 2) Disable consumer artifact management

```yaml
consumer:
	enabled: false
```

Behavior impact:
- Chart skips consumer artifact scaffolding paths.
- Use only when those artifacts are managed elsewhere.

### 3) Force redownload of specific published models

```yaml
publisher:
	forceRedownload:
		- "huggingface:org/repo@v1"
```

Behavior impact:
- Each entry is a source identity (`TYPE:REPO@REVISION`), not a catalog `name` —
  `name` is only a tenant-local label and can collide across tenants, so it is
  not a safe identifier for this list.
- Listed models bypass the manifest-pointer skip check on the next run and are
  redownloaded/republished even if already published at that revision.
- Defaults to an empty list; models are otherwise redownloaded only when their
  catalog `source.repo`/`revision` changes. See the
  [image README](../image/README.md#redownloadrepublish-skip-behavior) for the
  full skip/force contract, including the requirement that `revision` be an
  immutable pinned tag or commit SHA.
- Clear entries once the forced redownload is no longer needed.

## Release example

```bash
helm upgrade --install lm-serve-publisher . -n lm-serve-publisher --create-namespace \
	-f values.yaml
```

The publisher install does not require `TENANT`; the registry of tenant catalog entries already lives in `values.yaml`. The chart is intentionally installed in its own dedicated namespace such as `lm-serve-publisher`.

## Contract

This chart should not own model rollout state or Envoy configuration. It publishes the catalog; downstream runtime charts consume it.
