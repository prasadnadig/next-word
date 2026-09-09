# Publisher Component

This component publishes tenant model catalogs and model artifacts to object storage for downstream model runtime consumption. It includes:

- a publisher image that performs staged upload, verification, and promotion
- a Helm chart that schedules and configures publication lanes per tenant

Use the image docs when publication is run outside Helm (for example CI pipelines or external schedulers). Use the Helm docs when publication is managed as a Kubernetes CronJob.

## What This Component Does

- Owns source-of-truth model definitions in a global catalog (`catalogs/models.yaml`), with per-tenant selection files (`catalogs/<tenant>.yaml`) choosing which models are enabled for each tenant. See [Helm README](helm/README.md#ownership-boundary) for the full contract.
- Publishes serving artifacts to stable object-storage paths.
- Separates publication lifecycle from model serving lifecycle.
- Performs a local-disk preflight before model downloads to ensure there is room for the HF download stage plus a 20% headroom buffer over each model's `serving.pvcSize` estimate.
- Skips redownloading/republishing a model whose `source.repo`/`revision` already matches its last published manifest pointer, avoiding redundant Hugging Face downloads. Requires `revision` to be an immutable pinned tag or commit SHA. See [image README](image/README.md#redownloadrepublish-skip-behavior) for the skip/force-redownload contract.

## Local Disk Sizing And Recovery Notes

The publisher downloads Hugging Face model artifacts to a local temp directory before uploading to object storage. Because the model content is staged locally, the required local space is derived from each model catalog's `models[].serving.pvcSize` value, using the rule:

- required_local_bytes = ceil(serving.pvcSize * 1.2)

This is checked before downloads begin. If the available temp-disk space is below the peak model requirement, the publisher exits early instead of starting a partial or failed download. This avoids a long-running download that later fails due to insufficient local disk space.

Operator guidance:

- do not assume object-storage capacity alone is enough; local temp capacity is also required during the publish window
- use the largest model requirement as the safety threshold for a single model in a given run
- consider the total selected-run footprint as an operational planning number when multiple tenants and models are included in one invocation
- if capacity is insufficient, free space, point `TMPDIR` to a larger mounted volume, or reduce catalog scope with `--model` or `--max-models`

## Documentation Map

- [Helm chart README](helm/README.md)
- [Image/runtime README](image/README.md)
