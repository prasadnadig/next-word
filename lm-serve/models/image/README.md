# Model Reconciler Image

This image runs the model reconciliation engine that converts published tenant catalogs into runtime Kubernetes resources.

It can be used independently of Helm for custom automation, or paired with the models Helm chart for chart-managed workflows.

## What This Image Does

- Reads published tenant catalog data from object storage.
- Reconciles model StatefulSets and Services to match desired state.
- Generates and updates route artifacts consumed by the platform layer.
- Includes a standalone init-container helper (`sync_model_from_manifest.py`) that resolves manifest pointers and materializes model artifacts atomically.

## Dual Execution Modes (Single Image)

This image is intentionally used in two execution modes:

- Reconciler mode (default): container entrypoint runs `deploy_lm_serve_catalog.py` to reconcile catalog state into Kubernetes resources.
- Init-sync mode (overridden command): generated model StatefulSets run `python3 /app/sync_model_from_manifest.py` in a `model-sync` init container before vLLM starts.

Why this works safely:

- The image packages both scripts.
- Reconciler and init-sync responsibilities are separated by explicit command selection, not by embedded shell blocks.
- Init container completion is a startup gate: vLLM starts only after model artifacts are fully materialized and verified.

Current deployment convention:

- The models chart passes `--init-sync-image` and currently sets it to the same value as `modelReconciler.image`.
- This keeps one artifact to build and promote while preserving two distinct runtime behaviors.

## Build

```bash
docker build -t <REGISTRY>/vllm-catalog-deployer:0.1.0 .
```

## Push

```bash
docker push <REGISTRY>/vllm-catalog-deployer:0.1.0
```

## Run Through Make (from lm-serve)

```bash
make build-model-reconciler-image MODEL_RECONCILER_IMAGE_REGISTRY=<REGISTRY> MODEL_RECONCILER_IMAGE_REPOSITORY=vllm-catalog-deployer MODEL_RECONCILER_IMAGE_TAG=0.1.0
make push-model-reconciler-image MODEL_RECONCILER_IMAGE_REGISTRY=<REGISTRY> MODEL_RECONCILER_IMAGE_REPOSITORY=vllm-catalog-deployer MODEL_RECONCILER_IMAGE_TAG=0.1.0
```

## Helm Integration

When used with the models chart, set:

- `modelReconciler.enabled=true`
- `modelReconciler.image=<REGISTRY>/vllm-catalog-deployer:0.1.0`

See [../helm/README.md](../helm/README.md) for full chart-level configuration and operational guidance.
