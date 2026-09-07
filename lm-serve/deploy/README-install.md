# Model Deployment Reconciler Installation And Operations

This guide explains how to install and operate the vLLM serving stack model reconciler.

## What the model deployment reconciler does

`deploy_lm_serve_catalog.py` reconciles a shared model catalog into:

- One StatefulSet per enabled model (stable per-replica PVC cache).
- One Service per enabled model.
- Route data that the platform chart can consume through the shared route ConfigMap contract.

Because reconciliation is catalog-driven, model add/update/remove operations are done by editing one ConfigMap and rerunning the model-reconciler image Job (or enabling watch mode).

## Prerequisites

- Kubernetes cluster with NVIDIA GPU Operator already installed.
- `kubectl` access with permissions to create resources in target namespace.
- `yq` installed locally (required by smoke-test helper scripts).
- `helm` installed locally.
- Existing StorageClass suitable for model PVCs.
- Object storage bucket containing model artifacts under `<prefix>/current/`.

## Install sequence

Make-based shortcut:

```bash
cd ..
make apply-base TENANT=sample-env
make apply-serve-model TENANT=sample-env
```

Auth namespace selection:

- Default behavior: `AUTH_NAMESPACE` follows `TENANT` (for `TENANT=sample-env`, auth deploys to `sample-env`).
- To keep auth per workload namespace instead, run with `AUTH_NAMESPACE=<workload-namespace>`.
- To share one auth deployment across multiple workload namespaces, set the same `AUTH_NAMESPACE` value for each tenant.

Equivalent explicit workflow:

1. Create namespace

```bash
kubectl create namespace lm-serve
```

2. Apply split charts and namespace secrets

```bash
helm upgrade --install lm-serve-auth ../helm/lm-serve-auth -n lm-serve --create-namespace \
  -f ../helm/lm-serve-auth/values.yaml \
  --set-string authService.image=REPLACE_ME_REGISTRY/lm-serve-auth-service:0.1.0

helm upgrade --install lm-serve-platform ../helm/lm-serve-platform -n lm-serve --create-namespace \
  -f ../helm/lm-serve-platform/values.yaml

helm upgrade --install lm-serve-models ../helm/lm-serve-models -n lm-serve --create-namespace \
  -f ../helm/lm-serve-models/values.yaml \
  -f ../helm/lm-serve-models/values.sample-env.yaml \
  --set modelReconciler.enabled=false

kubectl -n lm-serve apply -f ../manifests/secrets.examples.yaml
```

If you are building the auth service image from source in this repository, use:

```bash
cd ..
make build-auth-image AUTH_IMAGE=<registry>/lm-serve-auth-service:0.1.0
make push-auth-image AUTH_IMAGE=<registry>/lm-serve-auth-service:0.1.0
make apply-auth-service AUTH_IMAGE=<registry>/lm-serve-auth-service:0.1.0
```

3. Generate initial auth materials for small-team tests

```bash
bash generate_test_auth_materials.sh --team-size 3 --jwt-sub dev-user --jwt-ttl-sec 3600
```

4. Update the auth secret using generated values

```bash
kubectl -n lm-serve edit secret lm-serve-auth-secrets
```

5. Build and push the model reconciler image

```bash
cd ..
make build-model-reconciler-image MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make push-model-reconciler-image MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
```

6. Run reconciliation once (in-cluster image Job)

```bash
cd ..
make apply-serve-model \
  TENANT=sample-env \
  MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
```

7. Verify resources

```bash
kubectl -n lm-serve get pods
kubectl -n lm-serve get statefulset
kubectl -n lm-serve get svc
```

8. Get edge endpoint

```bash
kubectl -n lm-serve get svc vllm-edge
```

9. If you want continuous in-cluster reconciliation, apply watch mode

```bash
helm upgrade --install lm-serve-models ../helm/lm-serve-models -n lm-serve \
  -f ../helm/lm-serve-models/values.yaml \
  -f ../helm/lm-serve-models/values.sample-env.yaml \
  --set modelReconciler.enabled=true \
  --set-string modelReconciler.args.watchIntervalSec=60 \
  --set-string modelReconciler.image=REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
```

## Watch mode (optional)

To continuously pick up ConfigMap changes:

```bash
cd ..
make deploy-watch-local \
  TENANT=sample-env \
  MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
```

## In-cluster model reconciler option (recommended for continuous model reconciliation)

Build and push the model deployment reconciler image:

```bash
docker build -t REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0 .
docker push REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
```

Enable model reconciler through Helm:

```bash
helm upgrade --install lm-serve-models ../helm/lm-serve-models -n lm-serve \
  -f ../helm/lm-serve-models/values.yaml \
  -f ../helm/lm-serve-models/values.sample-env.yaml \
  --set modelReconciler.enabled=true \
  --set-string modelReconciler.image=REPLACE_ME_REGISTRY/vllm-catalog-deployer:0.1.0
```

The model reconciler Job runs the model deployment reconciler in watch mode every 60 seconds internally.

Make-based variant:

```bash
cd ..
make build-model-reconciler-image MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make push-model-reconciler-image MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
make apply-model-reconciler TENANT=sample-env MODEL_RECONCILER_IMAGE=<registry>/vllm-catalog-deployer:0.1.0
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
make smoke-test-api-key API_KEY=<api-key>
make smoke-test-jwt JWT_TOKEN=<jwt>
make smoke-test-all API_KEY=<api-key> JWT_TOKEN=<jwt>
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
