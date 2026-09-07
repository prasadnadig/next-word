# Model Catalog Publisher Guide

This module packages a Python 3.12.12 model publisher that reads the shared model catalog and pushes artifacts to S3-compatible object storage using staging + manifest promotion.

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

Run against local catalog file:

```bash
python3 publish_model_catalog.py --catalog-file ../manifests/model-catalog.configmap.yaml --model mistral-7b-instruct
```

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
- Different cluster: mount or pass exported catalog YAML and use `--catalog-file`.

## Security hardening in Dockerfile

- Pinned Python 3.12.12 slim base.
- Non-root runtime user.
- Minimal OS package set.
- No shell-based secret echoing in script.

## Operational notes

- Keep source and destination credentials separate.
- Use bucket prefix scoping per model.
- Rotate keys and tokens.
- Validate promoted manifest before changing serving catalog entries.
