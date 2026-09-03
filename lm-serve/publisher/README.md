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

1. Push image to your registry.
2. Update image value in `../helm/lm-serve/values-base.yaml` or pass `PUBLISHER_IMAGE` to the Make target.
3. Ensure required Secrets exist in the target namespace.
4. Apply RBAC and ServiceAccount:

```bash
helm upgrade --install lm-serve ../helm/lm-serve -n lm-serve --create-namespace \
	-f ../helm/lm-serve/values-base.yaml \
	-f ../helm/lm-serve/values.sample-env.yaml \
	-f ../helm/lm-serve/values.sample-env.models.yaml \
	--set modelPublisherRbac.enabled=true
```

5. Apply CronJob:

```bash
helm upgrade --install lm-serve ../helm/lm-serve -n lm-serve \
	-f ../helm/lm-serve/values-base.yaml \
	-f ../helm/lm-serve/values.sample-env.yaml \
	-f ../helm/lm-serve/values.sample-env.models.yaml \
	--set modelPublisherCronjob.enabled=true \
	--set-string modelPublisherCronjob.image=REPLACE_ME_REGISTRY/lm-serve-model-publisher:0.1.0
```

Make-based variant:

```bash
make apply-model-publisher-rbac ENV=sample-env
make apply-model-publisher-cronjob ENV=sample-env PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

Model publisher deployment is supported only through Helm templates under `../helm/lm-serve/templates/`.

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
