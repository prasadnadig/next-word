# Model Catalog Publisher Guide

This module packages a Python 3.12.12 model publisher that reads tenant source catalogs and publishes both model artifacts and tenant catalog files to S3-compatible object storage.

## Why staging + promotion

Publishing to staging first avoids exposing partial model uploads. Promotion happens only after staged object verification.

Flow per model:

1. Download artifacts from source.
2. Upload to `<prefix>/_staging/<run-id>/`.
3. Verify object count in staging.
4. Copy to `<prefix>/current/`.
5. Write `<prefix>/manifests/<run-id>.json`.
6. Update `<prefix>/manifest.json` pointer.

## Local run (developer)

Set credentials:

```bash
export S3_ACCESS_KEY_ID="..."
export S3_SECRET_ACCESS_KEY="..."
export HF_TOKEN="..."
```

Run against local tenant catalog files:

```bash
python3 publish_model_catalog.py \
	--catalog-tenant-file sample-env=../helm/lm-serve-publisher/catalogs/sample-env.yaml \
	--model mistral-7b-instruct
```

## Source Catalog Input Modes

Use `--catalog-tenant-file` for file mode.

- Repeat the flag as `TENANT=PATH` pairs.
- This mode is ideal for local tests, CI, or cross-cluster runs.
- Example:

```bash
python3 publish_model_catalog.py \
	--catalog-tenant-file dev-west=./catalogs/dev-west.yaml \
	--catalog-tenant-file staging=./catalogs/staging.yaml
```

Use `--catalog-configmap` with `--catalog-tenant` for source-catalog ConfigMap mode.

- Repeat both flags in matching order.
- This mode is ideal for in-cluster publishing where source catalogs are already ConfigMaps.
- Example:

```bash
python3 publish_model_catalog.py \
	--catalog-namespace lm-serve \
	--catalog-configmap lm-serve-model-catalog-dev-west \
	--catalog-tenant dev-west \
	--catalog-configmap lm-serve-model-catalog-staging \
	--catalog-tenant staging
```

## Namespace Semantics

- `--catalog-namespace`:
	Namespace used only to read source catalog ConfigMaps in ConfigMap mode.
	This setting does not control where published tenant catalogs are written.

## Published Tenant Catalog Outputs

Published catalog object path semantics:

- `--published-catalogs-prefix`:
	Object storage prefix used for tenant catalog output.
- `--published-catalog-key`:
	Filename for each tenant catalog object.

Output path shape:

- `s3://<catalog.storage.bucket>/<published-catalogs-prefix>/<tenant>/<published-catalog-key>`

Runtime reconciliation reads this published object-storage catalog artifact, not the source catalog ConfigMap input.

`--catalog-tenant` is only valid with `--catalog-configmap` and is used to map each ConfigMap to its tenant label.

Input mode guardrails:

- `--catalog-tenant-file` mode and `--catalog-configmap` mode are mutually exclusive.
- One input mode is required.
- In ConfigMap mode, `--catalog-tenant` count must match `--catalog-configmap` count.

## Storage Validation Requirements

Catalog `storage` now requires explicit and valid values:

- `storage.bucket`: required, non-empty
- `storage.endpoint`: required, absolute `http://` or `https://` URL
- `storage.region`: required, non-empty, alphanumeric/hyphen (`[A-Za-z0-9-]`), max length 63

Missing or invalid values fail fast before publication starts. Helm rendering also fails fast when chart values provide invalid endpoint or region.

Publisher writes tenant catalog outputs to the same object storage target as model artifacts for that tenant catalog.

## Prune Staging Default

- Script default: `--prune-staging` is enabled by default.
- Disable explicitly with `--no-prune-staging`.
- Helm CronJob passes either `--prune-staging` or `--no-prune-staging` explicitly based on chart values, so behavior is consistent across local and in-cluster runs.

## Build container image

```bash
docker build -t lm-serve-model-publisher:0.1.0 .
```

Make-based variant from module root:

```bash
make build-model-publisher-image PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
make push-model-publisher-image PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

## Run in Kubernetes CronJob

The publisher supports per-catalog enablement via `consumer.catalogs[*].enabled`. Any entry with `enabled: false` is skipped by the Helm render and by the Python publisher job.

1. Push image to your registry.
2. Update image value in `../helm/lm-serve-publisher/values.yaml` or pass `PUBLISHER_IMAGE` to the Make target.
3. Ensure required Secrets exist in the target namespace.
4. Install the publisher chart in a dedicated namespace such as `lm-publisher`:

```bash
helm upgrade --install lm-serve-publisher ../helm/lm-serve-publisher -n lm-publisher --create-namespace \
	-f ../helm/lm-serve-publisher/values.yaml
```

5. Deploy the publisher image and schedule the CronJob:

```bash
helm upgrade --install lm-serve-publisher ../helm/lm-serve-publisher -n lm-publisher \
	-f ../helm/lm-serve-publisher/values.yaml \
	--set-string publisher.image=REPLACE_ME_REGISTRY/lm-serve-model-publisher:0.1.0
```

Make-based variant:

```bash
make apply-model-publisher PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

`TENANT` is not required for the publisher install itself because the publisher chart owns the canonical registry in `../helm/lm-serve-publisher/values.yaml`. Model publisher deployment is supported only through the dedicated `../helm/lm-serve-publisher/` chart.

## Cross-cluster operation

The model publisher can run in the same cluster as vLLM or in another cluster.

- Same cluster: read catalog by ConfigMap API directly.
- Different cluster: mount or pass tenant catalog YAML files and use `--catalog-tenant-file`.

In both cases, published tenant catalogs are written to object storage and consumed by runtime reconciliation from object storage.

## Security hardening in Dockerfile

- Pinned Python 3.12.12 slim base.
- Non-root runtime user.
- Minimal OS package set.
- No shell-based secret echoing in script.

## Operational notes

- Keep publication credentials least-privileged and rotated.
- Use bucket prefix scoping per model.
- Rotate keys and tokens.
- Validate promoted manifest before changing serving catalog entries.
