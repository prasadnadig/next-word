# Models Component

This component turns published tenant catalogs into runnable model-serving resources. It includes:

- a Helm chart for model runtime contracts and reconciler wiring
- a model reconciler image for catalog-driven rollout logic

Use the image docs when you want to run reconciliation outside Helm. Use the Helm docs when you want values-driven, chart-managed model runtime rollout.

## What This Component Does

- Reconciles model catalog intent into StatefulSets and Services.
- Produces route artifacts consumed by the platform edge layer.
- Supports one-shot or watch-style reconciliation workflows.

## Documentation Map

- [Helm chart README](helm/README.md)
- [Image/runtime README](image/README.md)
