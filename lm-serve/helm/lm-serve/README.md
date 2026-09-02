# lm-serve Helm Chart

This chart templates namespace-scoped resources used by the lm-serve scripts:

- Model catalog ConfigMap (`lm-serve-model-catalog`)
- Model publisher RBAC (`lm-serve-model-publisher`)
- Model publisher CronJob (`lm-serve-model-publisher`)
- Optional in-cluster model reconciler Job (`vllm-catalog-model-reconciler`)

For guidance on running multiple environments on the same cluster, see [multiple-environments.md](multiple-environments.md).

## Render and inspect

```bash
helm template lm-serve . -n lm-serve \
  -f values-base.yaml \
  -f values.sample-env.yaml \
  -f values.sample-env.models.yaml
```

## Install base resources

```bash
helm upgrade --install lm-serve . -n lm-serve --create-namespace \
  -f values-base.yaml \
  -f values.sample-env.yaml \
  -f values.sample-env.models.yaml \
  --set modelPublisherCronjob.enabled=false \
  --set modelReconciler.enabled=false
```

## Enable model reconciler

```bash
helm upgrade --install lm-serve . -n lm-serve \
  -f values-base.yaml \
  -f values.sample-env.yaml \
  -f values.sample-env.models.yaml \
  --set modelReconciler.enabled=true \
  --set-string modelReconciler.image=<registry>/vllm-catalog-deployer:0.1.0
```

## Enable model publisher CronJob

```bash
helm upgrade --install lm-serve . -n lm-serve \
  -f values-base.yaml \
  -f values.sample-env.yaml \
  -f values.sample-env.models.yaml \
  --set modelPublisherCronjob.enabled=true \
  --set-string modelPublisherCronjob.image=<registry>/lm-serve-model-publisher:0.1.0
```

## Use environment model overrides

The chart includes [values.sample-env.models.yaml](values.sample-env.models.yaml) for sample environment model list overrides.

```bash
helm upgrade --install lm-serve . -n lm-serve \
  -f values-base.yaml \
  -f values.sample-env.yaml \
  -f values.sample-env.models.yaml
```

For environment-specific settings, create separate override files and pass them with additional `-f` arguments.

Example smoke-test environment using ultra-small Hugging Face models:

```bash
helm template lm-serve . -n lm-serve \
  -f values-base.yaml \
  -f values.tiny-smoke.yaml \
  -f values.tiny-smoke.models.yaml
```

Example small-but-representative environment using company-backed models:

```bash
helm template lm-serve . -n lm-serve \
  -f values-base.yaml \
  -f values.small-representative.yaml \
  -f values.small-representative.models.yaml
```

## Add a new environment

All three files are required for a complete environment profile:

1. `values-base.yaml`: shared defaults and validation rules used by every environment.
2. `values.<env>.yaml`: environment-specific infrastructure and deployment settings.
3. `values.<env>.models.yaml`: model catalog entries and per-model serving settings.

Example for `dev-west`:

```bash
cp values.sample-env.yaml values.dev-west.yaml
cp values.sample-env.models.yaml values.dev-west.models.yaml
helm template lm-serve . -n lm-serve \
  -f values-base.yaml \
  -f values.dev-west.yaml \
  -f values.dev-west.models.yaml
```
