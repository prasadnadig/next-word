# Script A Installation And Operations

This guide explains how to install and operate the vLLM serving stack model reconciler.

## What Script A does

`deploy_lm_serve_catalog.sh` reconciles a shared model catalog into:

- One StatefulSet per enabled model (stable per-replica PVC cache).
- One in-cluster auth service (API key or JWT).
- One Envoy edge deployment exposed via Service type `LoadBalancer` (default).

Because reconciliation is catalog-driven, model add/update/remove operations are done by editing one ConfigMap and re-running the script (or running with watch mode).

## Prerequisites

- Kubernetes cluster with NVIDIA GPU Operator already installed.
- `kubectl` access with permissions to create resources in target namespace.
- `yq` installed locally.
- `helm` installed locally.
- Existing StorageClass suitable for model PVCs.
- Object storage bucket containing model artifacts under `<prefix>/current/`.

## Install sequence

Make-based shortcut:

```bash
cd ..
make apply-base
make deploy-once
```

Equivalent explicit workflow:

1. Create namespace

```bash
kubectl create namespace lm-serve
```

2. Apply base Helm chart resources and secrets

```bash
helm upgrade --install lm-serve ../helm/lm-serve -n lm-serve --create-namespace \
  -f ../helm/lm-serve/values-base.yaml \
  -f ../helm/lm-serve/values.sample-env.yaml \
  -f ../helm/lm-serve/values.sample-env.models.yaml \
  --set modelPublisherCronjob.enabled=false \
  --set modelReconciler.enabled=false
kubectl -n lm-serve apply -f ../manifests/secrets.examples.yaml
```

3. Generate initial auth materials for small-team tests

```bash
bash generate_test_auth_materials.sh --team-size 3 --jwt-sub dev-user --jwt-ttl-sec 3600
```

4. Update the auth secret using generated values

```bash
kubectl -n lm-serve edit secret lm-serve-auth-secrets
```

5. Run reconciliation once

```bash
bash deploy_lm_serve_catalog.sh \
  --namespace lm-serve \
  --catalog-configmap lm-serve-model-catalog \
  --catalog-key models.yaml \
  --edge-service-type LoadBalancer
```

6. Verify resources

```bash
kubectl -n lm-serve get pods
kubectl -n lm-serve get statefulset
kubectl -n lm-serve get svc
```

7. Get edge endpoint

```bash
kubectl -n lm-serve get svc vllm-edge
```

## Watch mode (optional)

To continuously pick up ConfigMap changes:

```bash
bash deploy_lm_serve_catalog.sh \
  --namespace lm-serve \
  --catalog-configmap lm-serve-model-catalog \
  --catalog-key models.yaml \
  --watch-interval-sec 60
```

## In-cluster model reconciler option (recommended for continuous model reconciliation)

Build and push Script A image:

```bash
docker build -t REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0 .
docker push REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
```

Enable model reconciler through Helm:

```bash
helm upgrade --install lm-serve ../helm/lm-serve -n lm-serve \
  -f ../helm/lm-serve/values-base.yaml \
  -f ../helm/lm-serve/values.sample-env.yaml \
  -f ../helm/lm-serve/values.sample-env.models.yaml \
  --set modelReconciler.enabled=true \
  --set-string modelReconciler.image=REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
```

The model reconciler Job runs Script A in watch mode every 60 seconds internally.

Make-based variant:

```bash
cd ..
make build-deployer-image DEPLOYER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make push-deployer-image DEPLOYER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make apply-model-reconciler ENV=sample-env DEPLOYER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
```

## Smoke test all enabled models

Use the dedicated smoke-test client script:

```bash
bash smoke_test_lm_serve_catalog.sh \
  --namespace lm-serve \
  --catalog-configmap lm-serve-model-catalog \
  --catalog-key models.yaml \
  --api-key "<YOUR_API_KEY>" \
  --jwt-token "<YOUR_JWT>"
```

If you want to validate one auth mode only, provide only `--api-key` or only `--jwt-token`.

Make-based variant:

```bash
cd ..
make smoke-api-key API_KEY=<api-key>
make smoke-jwt JWT_TOKEN=<jwt>
make smoke-both API_KEY=<api-key> JWT_TOKEN=<jwt>
```

## Catalog change behavior

- Add enabled model entry: creates new model StatefulSet and Service.
- Modify model settings: updates corresponding StatefulSet.
- Disable/remove model: deletes stale model StatefulSet and Service.

## Security notes

- Auth credentials come from Kubernetes Secret, not inline manifests.
- Pods run as non-root and drop Linux capabilities.
- Keep API keys and JWT secret rotated.
- Use least-privilege object-storage credentials.

## Multi-cloud portability notes

- Data plane is S3-compatible; switch endpoint and credentials to move clouds.
- StatefulSet/PVC behavior remains standard Kubernetes.
- If migrating cluster providers, update model `serving.nodeSelector`, `serving.tolerations`, `serving.affinity`, and storage class conventions in catalog/deploy script.
