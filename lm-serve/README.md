# LM Serve Bootstrap Modules

This directory provides a clean, modular baseline for model publication and vLLM serving on Linode today, while remaining portable to any S3-compatible cloud later.

## Modules

- `Makefile`
  - Convenience targets for build, deploy, model reconciliation, and smoke testing.

- `helm/lm-serve-auth/`
  - Dedicated auth service chart for runtime, service, and secret wiring.

- `auth-service/`
  - Tracked Python auth service source, dependencies, and Dockerfile for production image delivery.

- `helm/lm-serve-platform/`
  - Dedicated Envoy platform chart for edge rollout and auth wiring.

- `helm/lm-serve-models/`
  - Dedicated model-runtime chart that consumes the published catalog and owns rollout plus route-data generation.

- `helm/lm-serve-publisher/`
  - Dedicated model publisher chart that owns the source model catalogs, publication CronJob, RBAC, and publishing runtime.

- `deploy/`
  - `deploy_lm_serve_catalog.py`: Model deployment reconciler that reconciles catalog entries into one StatefulSet per model plus shared Envoy edge.
  - `generate_test_auth_materials.sh`: Helper for initial API key/JWT bootstrap for small-team testing.
  - `smoke_test_lm_serve_catalog.sh`: End-to-end smoke tester for all enabled models using API key/JWT.
  - `Dockerfile`: Optional in-cluster runtime image for the model deployment reconciler.
  - `README-install.md`: Detailed cluster installation and operations guide.
  - `README-inference-usage.md`: End-user usage guide (API key/JWT, request examples).

- `publisher/`
  - `publish_model_catalog.py`: Model catalog publisher. Downloads source artifacts and publishes with staging + manifest promotion.
  - `Dockerfile`: Hardened Python 3.12.12 runtime image.
  - `requirements.txt`: Script dependencies.
  - `README.md`: Build, deploy, and cron operation guide.

- `helm/lm-serve/`
  - Legacy shared environment overlays and decomposition notes used during the chart split.

- `manifests/`
  - `secrets.examples.yaml`: Example Secrets for object storage, auth, and Hugging Face token.
  - `model-catalog.configmap.yaml`: Local catalog reference for non-cluster publisher runs.

## Cloud-extendable approach

The implementation is intentionally cloud-neutral at the data layer:

- Uses S3-compatible APIs, not Linode-specific SDK calls.
- Uses endpoint + bucket + region from catalog or env.
- Keeps serving and publishing logic independent of one specific cluster.

To move from Linode to another cloud, update storage endpoint, credentials, and any cluster-specific labels/storage classes.

## Architecture proposal: split charts

For independent versioning and release of auth, platform software, and model rollout, see:

- `helm/lm-serve/chart-decomposition-proposal.md`

## Environment values model

The consumer-side model chart uses environment overlays only:

1. `helm/lm-serve-models/values.yaml`: shared chart defaults, naming, and validation behavior.
2. `helm/lm-serve-models/values.<env>.yaml`: environment-specific storage endpoints, bucket names, and feature toggles.

The source-of-truth catalog definitions live under the publisher chart instead:

- `helm/lm-serve-publisher/catalogs/<env>.yaml`

To add a new environment, copy the publisher catalog and the model runtime override file, then rename them to your environment name.

## Quick start with Make targets

```bash
make help
make apply-base ENV=sample-env
make apply-serve-model ENV=sample-env
make happy-apply ENV=sample-env API_KEY=<api-key>
```

## Helm customization workflow

Render templates locally:

```bash
make render-helm-template ENV=sample-env
```

Apply chart with an override file:

```bash
make apply-base ENV=sample-env
```

Override individual values from CLI:

```bash
make apply-model-reconciler MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make apply-model-publisher PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

For continuous in-cluster model reconciliation and periodic model publishing, build/push images and apply manifests:

```bash
make build-model-reconciler-image MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make push-model-reconciler-image MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make apply-model-reconciler ENV=sample-env MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make apply-serve-model ENV=sample-env MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0

make build-model-publisher-image PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
make push-model-publisher-image PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
make apply-model-publisher PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

Publisher install note:

- The publisher chart is installed in its own dedicated namespace, typically `lm-publisher`.
- `ENV` is not required for `make apply-model-publisher` because the canonical publisher catalog registry already lives in `helm/lm-serve-publisher/values.yaml` and the env-specific catalog files are selected there.
- `ENV` remains necessary for consumer-side chart installs such as `apply-base` and `apply-model-reconciler`.

Auth namespace behavior:

- Default: `AUTH_NAMESPACE` follows `ENV`.
- Override: set `AUTH_NAMESPACE=<namespace>` to use a shared auth deployment across multiple environments.
- If `AUTH_NAMESPACE` differs from `NAMESPACE`, `make apply-base` applies example secrets to both namespaces.

To pass additional Helm options (for example, extra `-f` files or `--set` flags), use `HELM_EXTRA_ARGS`.

Note: Helm-related Make targets require `ENV=<name>` and automatically layer `helm/lm-serve-models/values.yaml` and `helm/lm-serve-models/values.<env>.yaml`. The source catalog metadata for that environment is expected under `helm/lm-serve-publisher/catalogs/<env>.yaml`.

Model publisher and model reconciler deployment are supported through the dedicated `helm/lm-serve-publisher/` and `helm/lm-serve-models/` charts.
