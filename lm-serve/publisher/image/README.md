# Model Catalog Publisher Guide

This component publishes model artifacts and tenant catalog outputs to S3-compatible object storage using immutable shared artifacts plus manifest-pointer activation.

It is intended for production use through the publisher Helm chart in [../helm](../helm), and can also be run locally for validation.

## Production Design Overview

Catalog inputs are split into two layers:

- **Global catalog** (one file/ConfigMap, shared across all tenants): defines
  every known model once, keyed by a globally unique `name` — `source`,
  `storage.prefix`, and a base `serving` block.
- **Tenant selection** (one file/ConfigMap per tenant): lists which global
  model `name`s are enabled for that tenant, with an optional tenant-local
  `enabled` flag and `servingOverrides` (deep-merged onto the global `serving`
  block for that tenant only).

A model must be `enabled: true` in both the global catalog and the tenant
selection to be published for that tenant. This split is an input-side
concern only — the published tenant catalog object written to object storage
is unchanged.

The publisher is a catalog-driven release pipeline with explicit safety gates:

1. Read the global catalog, then each tenant's selection (file mode or ConfigMap mode), and resolve them into fully-specified per-tenant model lists.
2. Validate explicit storage config and run inputs.
3. Download model artifacts from source providers.
4. Upload artifacts to immutable shared content-addressed keys.
5. Write immutable run manifest and update active manifest pointer.
6. Publish per-tenant catalog output for model runtime consumers.

### End-to-end flow

```mermaid
flowchart TD
	A[Stage 0: Catalog Intent\nGlobal catalog (--global-catalog-file/--global-catalog-configmap)\nplus per-tenant selections:\n- --catalog-tenant-file TENANT=PATH\n- --catalog-configmap + --catalog-tenant\nEach selection is resolved against the global catalog by model name] --> B

	B[Stage 1: Preflight Validation\nValidates explicit storage args:\n- --storage-bucket non-empty\n- --storage-endpoint absolute http/https\n- --storage-region valid token\nIf invalid, run fails fast before any publish side effects] --> C

	C[Stage 2: Acquire Artifacts\nPublisher fetches model assets from configured source\nWhat users should know:\n- Source credentials must be present\n- Network egress to source must be allowed\n- Large models can increase runtime significantly] --> D

	D[Stage 3: Shared Artifact Upload\nUpload target: <shared-prefix>/<sha256>/<relative-path>\nWhat users should know:\n- Artifact keys are immutable and content-addressed\n- Existing objects are reused\n- No duplicate blob uploads when checksums match] --> E

	E[Stage 4: Manifest Publication\nWrites <model-prefix>/manifests/<run-id>.json\nUpdates <model-prefix>/manifest.json pointer\nWhat users should know:\n- Run manifest is immutable audit evidence\n- Pointer flip is the visibility boundary] --> F

	F[Stage 5: Tenant Catalog Output\nWrites published catalog object:\nS3 path: <published-catalogs-prefix>/<tenant>/<published-catalog-key>\nConsumed later by model runtime reconciliation\nWhat users should know:\n- Runtime reads published catalog, not source catalog] --> G
	G[Consumers: Model Reconciler\nReads tenant published catalog from object storage\nReconciles model runtime resources separately]:::consumer

	classDef consumer fill:#f2f7ff,stroke:#4a74c9,stroke-width:1px;
```

### Manifest-pointer publication contract

Flow per model publication run:

1. Download artifacts from source.
2. Upload each artifact to `<shared-artifacts-prefix>/<sha256>/<relative-path>` when missing.
3. Write `<model-prefix>/manifests/<run-id>.json`.
4. Update `<model-prefix>/manifest.json` pointer.

Operator expectations:

- Treat shared artifact objects as immutable blobs.
- Treat run manifests as immutable audit history.
- Treat the manifest pointer update as the serving visibility boundary.

## What To Configure For Production

### Required runtime secrets and credentials

Set credentials as environment variables (for local runs) or from Kubernetes Secrets (for CronJob runs):

```bash
export S3_ACCESS_KEY_ID="..."
export S3_SECRET_ACCESS_KEY="..."
export HF_TOKEN="..."
```

What to know:

- Use dedicated least-privilege credentials for publisher.
- Do not reuse broad admin object-storage credentials.
- Rotate credentials regularly and on incident.

### Source catalog input mode

Use exactly one input mode. Both modes require the global catalog plus one or more tenant selections.

Mode A: file mode with `--global-catalog-file` + `--catalog-tenant-file`

- `--global-catalog-file PATH` points at the shared global catalog (required in file mode).
- Repeat `--catalog-tenant-file` as `TENANT=PATH` pairs, where PATH is a tenant *selection* file (model names + enabled/servingOverrides), not a full catalog.
- Best for local tests, CI, and cross-cluster publication.

```bash
python3 publish_model_catalog.py \
	--storage-bucket my-lm-serve-models \
	--storage-endpoint https://us-ord-1.linodeobjects.com \
	--storage-region us-east-1 \
	--global-catalog-file ../helm/catalogs/models.yaml \
	--catalog-tenant-file sample-env=../helm/catalogs/sample-env.yaml \
	--model mistral-7b-instruct
```

```bash
python3 publish_model_catalog.py \
	--storage-bucket my-lm-serve-models \
	--storage-endpoint https://us-ord-1.linodeobjects.com \
	--storage-region us-east-1 \
	--global-catalog-file ./catalogs/models.yaml \
	--catalog-tenant-file dev-west=./catalogs/dev-west.yaml \
	--catalog-tenant-file staging=./catalogs/staging.yaml
```

Mode B: ConfigMap mode with `--global-catalog-configmap`, `--catalog-configmap`, and `--catalog-tenant`

- `--global-catalog-configmap NAME` points at the shared global catalog ConfigMap (required in ConfigMap mode).
- Repeat `--catalog-configmap`/`--catalog-tenant` in matching order for tenant selections.
- Best for in-cluster publication where source catalogs are already in Kubernetes.

```bash
python3 publish_model_catalog.py \
	--storage-bucket my-lm-serve-models \
	--storage-endpoint https://us-ord-1.linodeobjects.com \
	--storage-region us-east-1 \
	--catalog-namespace lm-serve-publisher \
	--global-catalog-configmap lm-serve-model-catalog-global \
	--catalog-configmap lm-serve-model-catalog-dev-west \
	--catalog-tenant dev-west \
	--catalog-configmap lm-serve-model-catalog-staging \
	--catalog-tenant staging
```

Namespace semantic note:

- `--catalog-namespace` is only for reading source ConfigMaps.
- It does not control where published tenant catalogs are written.

Storage semantic note:

- Source catalog payloads are model-only and do not carry object-storage coordinates.
- Publisher storage destination is always provided explicitly through `--storage-bucket`, `--storage-endpoint`, and `--storage-region` (or Helm values that render these args).

### Output path contract

Published tenant catalog object path:

- `s3://<storage-bucket>/<published-catalogs-prefix>/<tenant>/<published-catalog-key>`

Model runtime reconciliation consumes this published object, not the source catalog input.

### Input guardrails

- `--catalog-tenant-file` mode and `--catalog-configmap` mode are mutually exclusive.
- One input mode is required.
- `--global-catalog-file` is required in file mode; `--global-catalog-configmap` is required in ConfigMap mode.
- In ConfigMap mode, `--catalog-tenant` count must match `--catalog-configmap` count.
- A tenant selection entry referencing a model `name` not present in the global catalog fails fast with a clear error naming the tenant and the unknown model.

### Storage validation requirements

Storage CLI/config values must be valid:

- `--storage-bucket`: required, non-empty
- `--storage-endpoint`: required, absolute `http://` or `https://` URL
- `--storage-region`: required, non-empty, alphanumeric/hyphen (`[A-Za-z0-9-]`), max length 63

Invalid storage values fail fast before publication starts.

### Artifact retention policy

- Keep lifecycle/retention policies aligned to audit and cost goals.
- Shared artifact objects are content-addressed and can be reused across runs.

### Redownload/republish skip behavior

Model artifacts do not change often, so re-downloading from Hugging Face on every
scheduled run is wasteful. Before downloading, the publisher checks the model's
existing manifest pointer at `<storage-prefix>/manifest.json`:

- If a pointer already exists with the same `source_repo` and `source_revision`,
  the model is skipped entirely (no download, no upload).
- Otherwise (no pointer, or repo/revision changed), publication proceeds normally.

This requires `models[].source.revision` in the global catalog to be an **immutable**
pinned tag or commit SHA. A moving branch like `main` will never be detected as
changed, so the publisher would keep serving stale content indefinitely. Pin
`revision` to a specific commit SHA or release tag.

To force a redownload regardless of the pointer (for example after confirming
upstream content changed under the same tag, or to recover from a bad
publish), pass `--force-redownload TYPE:REPO@REVISION` (repeatable), e.g.
`--force-redownload huggingface:org/repo@v1`. This is a source-identity string
rather than the catalog `name` so it keeps matching correctly even if a model
is renamed in the global catalog. Clear the flag/list once the redownload is
no longer needed.

### Global catalog and tenant selection

Catalog input is split into two files/ConfigMaps:

- **Global catalog** (`--global-catalog-file`/`--global-catalog-configmap`): the
  single source of truth for every model. Each entry has a `name` that is
  **unique across the whole catalog** (parsing fails fast on a duplicate name
  or on two models sharing the same `storage.prefix`), plus `source`,
  `storage.prefix`, and a base `serving` block. A model's top-level `enabled`
  here is a global retire/block switch: `false` means no tenant can publish it.
- **Tenant selection** (`--catalog-tenant-file`/`--catalog-configmap` +
  `--catalog-tenant`): a list of `{name, enabled, servingOverrides}` entries
  referencing global catalog names. `enabled` here is tenant-local (defaults to
  `true`); `servingOverrides` is deep-merged onto the global `serving` block
  for that tenant's resolved model only (nested mappings like `nodeSelector`
  merge key-by-key, everything else — including lists like `tolerations` or
  `extraArgs` — is replaced wholesale when overridden).

A model publishes for a tenant only if it is `enabled: true` at **both**
levels. Referencing a `name` that doesn't exist in the global catalog is a
fail-fast error identifying the tenant and the unknown name.

### Local disk space preflight for model downloads

Model catalogs encode a rough artifact footprint in `models[].serving.pvcSize`. The publisher uses that value as the local-disk estimate before it begins a Hugging Face download, because the model is first downloaded to the local temp filesystem and only then uploaded to object storage.

The effective preflight requirement is:

- required_local_bytes = ceil(serving.pvcSize * 1.2)
- 20% headroom is added on top of the catalog pvcSize estimate to absorb temporary staging, extracted files, and filesystem overhead

Examples:

- `pvcSize: 120Gi` -> required local space ~ `144Gi`
- `pvcSize: 150Gi` -> required local space ~ `180Gi`

The script checks the free space on the active temp filesystem before starting downloads. If insufficient space is available, it exits early with a clear error explaining:

- the temp directory being used
- the free space currently available
- the required peak model footprint
- the total selected-run footprint across all chosen catalogs/models
- recovery steps

This is important because the publisher processes selected models sequentially in a loop, so the operational peak is the largest individual model's buffered estimate, even though the total run estimate is also shown for planning.

Operational guidance:

- ensure the temp filesystem has enough room for the largest selected model before starting publication
- set `TMPDIR` to a larger mounted volume if the default temp filesystem is too small
- if needed, reduce scope with `--model` or `--max-models` to keep publication within available local capacity
- in the CronJob deployment, `/tmp` is an unbounded `emptyDir` volume (see [../helm/templates/cronjob.yaml](../helm/templates/cronjob.yaml)); it has no chart-enforced size limit and is bounded only by the free disk space on the Kubernetes node the pod lands on

Recovery path when the check fails:

1. free enough space on the current temp volume or move `TMPDIR` to a larger mounted disk
2. rerun with a narrower catalog scope or fewer models
3. retry publication only after the disk-capacity preflight passes

## How To Run Securely

### Build image

```bash
docker build -t lm-serve-model-publisher:0.1.0 .
```

Make-based variant from [lm-serve](../../):

```bash
make build-model-publisher-image PUBLISHER_IMAGE_REGISTRY=<registry> PUBLISHER_IMAGE_REPOSITORY=lm-serve-model-publisher PUBLISHER_IMAGE_TAG=0.1.0
make push-model-publisher-image PUBLISHER_IMAGE_REGISTRY=<registry> PUBLISHER_IMAGE_REPOSITORY=lm-serve-model-publisher PUBLISHER_IMAGE_TAG=0.1.0
```

### Deploy with Helm CronJob (recommended for production)

The publisher supports per-catalog enablement via `consumer.catalogs[*].enabled`. Entries with `enabled: false` are skipped by Helm render and publisher execution.

1. Push image to your registry.
2. Set publisher image in `../helm/values.yaml` or pass `PUBLISHER_IMAGE_REGISTRY`/`PUBLISHER_IMAGE_REPOSITORY`/`PUBLISHER_IMAGE_TAG`.
3. Ensure required Secrets exist in the target namespace.
4. Install/upgrade publisher chart in a dedicated namespace such as `lm-serve-publisher`.

```bash
helm upgrade --install lm-serve-publisher ../helm -n lm-serve-publisher --create-namespace \
	-f ../helm/values.yaml
```

```bash
helm upgrade --install lm-serve-publisher ../helm -n lm-serve-publisher \
	-f ../helm/values.yaml \
	--set-string publisher.image.registry=<registry> \
	--set-string publisher.image.repository=lm-serve-model-publisher \
	--set-string publisher.image.tag=0.1.0
```

Make-based variant:

```bash
make apply-model-publisher PUBLISHER_IMAGE_REGISTRY=<registry> PUBLISHER_IMAGE_REPOSITORY=lm-serve-model-publisher PUBLISHER_IMAGE_TAG=0.1.0
```

`TENANT` is not required for publisher chart install because tenant publication lanes are configured by `consumer.catalogs` in `../helm/values.yaml`.

## Security Model And Production Controls

### Identity and access

- Use distinct object-storage credentials for publisher.
- Scope IAM/policy to only required bucket prefixes.
- Deny wildcard write outside publication prefixes.

### Secret handling

- Inject credentials from Kubernetes Secret references.
- Avoid hardcoded credentials in values files and command history.
- Rotate keys and tokens on a defined schedule.

### Network and transport

- Use HTTPS endpoints for object storage.
- Restrict egress to approved object-storage and model-source endpoints.
- Apply namespace-level network policy where available.

### Artifact safety

- Upload only immutable shared artifacts and immutable run manifests.
- Treat manifest pointer update as the publication visibility boundary.
- Retain run manifests for traceability and incident response.

### Runtime hardening

Current image hardening includes:

- Pinned Python 3.12.12 slim base.
- Non-root runtime user.
- Minimal OS package set.
- No shell-based secret echoing in script.

## Operational Runbook

### Pre-deploy checklist

- Confirm source catalogs and tenant mappings are correct.
- Confirm object-storage endpoint, region, and bucket values.
- Confirm credentials exist and have least privilege.
- Confirm lifecycle policy for shared-artifact, manifest-history, and published-catalog prefixes.

### During deploy

- Monitor CronJob/pod logs for preflight and verification outcomes.
- Treat verification failure as a stop signal; do not force promotion.
- Re-run only after correcting catalog, credentials, or source availability.

### Post-deploy checks

- Confirm `<prefix>/manifest.json` points to the expected run.
- Confirm expected tenant catalog outputs exist.
- Confirm model runtime reconciler can consume the new published catalog.

### Rollback approach

- Use previous manifest history to identify last known-good run.
- Re-point current manifest/published catalog only through controlled operational procedure.
- Re-run publication with corrected input when possible to restore forward state.

## Cross-cluster operation

The publisher can be deployed in the same cluster as the model reconciler or in a different cluster.

Preferred operating model:

- Deploy source catalogs with the publisher and let publisher read them locally.
- Publish tenant catalog outputs and manifest data to the shared object-storage location.
- Let the model reconciler read only from object storage.

Cross-cluster contract:

- Publisher writes published tenant catalogs, manifest pointers, and immutable run manifests to object storage.
- Model reconciler reads the published tenant catalog from object storage and materializes model artifacts by following each model's manifest pointer.
- There is no publisher-to-reconciler Kubernetes dependency in either same-cluster or cross-cluster deployments.
- Cross-cluster concerns are limited to both sides writing to and reading from the same object-storage locations.
