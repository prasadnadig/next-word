# LM Serve Bootstrap Modules

This directory provides a clean, modular baseline for model publication and vLLM serving on Linode today, while remaining portable to any S3-compatible cloud later.

## Modules

- `Makefile`
  - Convenience targets for build, deploy, model reconciliation, and smoke testing.

- `helm/lm-serve/`
  - Helm chart for templated customization of catalog, model publisher, and model reconciler resources.
  - `values-base.yaml`: Shared base values used for every environment.
  - `values.sample-env.yaml`: Example override values for an environment profile named `sample-env`.
  - `values.sample-env.models.yaml`: Example model list override values for `sample-env`.

- `deploy/`
  - `deploy_lm_serve_catalog.sh`: Script A. Model reconciler script that reconciles catalog entries into one StatefulSet per model plus shared Envoy edge.
  - `generate_test_auth_materials.sh`: Helper for initial API key/JWT bootstrap for small-team testing.
  - `smoke_test_lm_serve_catalog.sh`: End-to-end smoke tester for all enabled models using API key/JWT.
  - `Dockerfile`: Optional in-cluster model reconciler image build for Script A.
  - `README-install.md`: Detailed cluster installation and operations guide.
  - `README-inference-usage.md`: End-user usage guide (API key/JWT, request examples).

- `publisher/`
  - `publish_model_catalog.py`: Script B model publisher. Downloads source artifacts and publishes with staging + manifest promotion.
  - `Dockerfile`: Hardened Python 3.12.12 runtime image.
  - `requirements.txt`: Script dependencies.
  - `README.md`: Build, deploy, and cron operation guide.

- `manifests/`
  - `secrets.examples.yaml`: Example Secrets for object storage, auth, and Hugging Face token.
  - `model-catalog.configmap.yaml`: Local catalog reference for non-cluster publisher runs.

## Cloud-extendable approach

The implementation is intentionally cloud-neutral at the data layer:

- Uses S3-compatible APIs, not Linode-specific SDK calls.
- Uses endpoint + bucket + region from catalog or env.
- Keeps serving and publishing logic independent of one specific cluster.

To move from Linode to another cloud, update storage endpoint, credentials, and any cluster-specific labels/storage classes.

## Environment values model

Each environment should use exactly three values files:

1. `values-base.yaml`: shared chart defaults, naming, and validation behavior.
2. `values.<env>.yaml`: environment-specific storage endpoints, bucket names, and feature toggles.
3. `values.<env>.models.yaml`: model list and per-model serving configuration.

To add a new environment, copy both `sample-env` files and rename them to your environment name.

## Quick start with Make targets

```bash
make help
make apply-base
make deploy-once
make happy-path API_KEY=<api-key>
```

## Helm customization workflow

Render templates locally:

```bash
make helm-template ENV=sample-env
```

Apply chart with an override file:

```bash
make helm-upgrade-base ENV=sample-env
```

Override individual values from CLI:

```bash
make apply-model-reconciler DEPLOYER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make apply-model-publisher-cronjob PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

For continuous in-cluster model reconciliation and periodic model publishing, build/push images and apply manifests:

```bash
make build-deployer-image DEPLOYER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make push-deployer-image DEPLOYER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make apply-model-reconciler ENV=sample-env DEPLOYER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0

make build-model-publisher-image PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
make push-model-publisher-image PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
make apply-model-publisher-rbac ENV=sample-env
make apply-model-publisher-cronjob ENV=sample-env PUBLISHER_IMAGE=<registry>/lm-serve-model-publisher:0.1.0
```

To pass additional Helm options (for example, extra `-f` files or `--set` flags), use `HELM_EXTRA_ARGS`.

Note: Helm-related Make targets require `ENV=<name>` and automatically layer `values-base.yaml`, `values.<env>.yaml`, and `values.<env>.models.yaml`.

Model publisher and model reconciler deployment are supported only through Helm templates under `helm/lm-serve/templates/`.
