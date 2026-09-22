# AGENTS-MAKEFILE-STYLE.md

## Purpose and Operating Model

Makefiles are the operator-facing workflow for each module. They should make the
intended path from source to a validated deployment obvious, repeatable, and safe:

```text
check tools -> build/render -> publish (when required) -> apply/install
            -> reconcile -> smoke/validate
```

Keep local development, registry publishing, cluster operations, and teardown
available as separate targets. A module may package zero or more images and zero or
more charts; do not invent a fake artifact merely to fit this workflow.

## File Layout and Shell Behavior

- Set `SHELL := /bin/bash` when recipes use Bash features such as `[[ ... ]]`,
  regular-expression matching, `pipefail`, or `trap`.
- Put user-overridable variables near the top of the file, before targets.
- Group variables by concern, in this order when applicable:
  1. core deployment defaults
  2. container image overrides
  3. Helm release, chart, and values wiring
  4. chart packaging and registry wiring
  5. chart-source selection
  6. smoke-test inputs
  7. extra arguments and safety flags
- Use `?=` for defaults so every operational input can be overridden with
  `make VAR=value` without editing the file.
- Prefer derived variables for composed values, such as `IMAGE`, `CHART_PACKAGE`,
  `CHART_OCI_REF`, and reusable Helm argument lists.
- Keep recipes short and compose reusable checks and helpers through `$(MAKE)`.
- For a recipe containing multiple dependent shell commands, use one shell context
  with `set -euo pipefail`; preserve cleanup traps and fail immediately on errors.

## Shared Makefile Includes

- Each repository should keep shared Makefile mechanics in a versioned local `mk/`
  directory. Because `ground` and `next-word` are separate repositories, do not
  depend on relative paths that cross repository boundaries.
- Use a shared include such as `mk/common.mk` for `SHELL`, derived defaults, tool
  checks, `check-tools`, `confirm-target-cluster`, common artifact policy, and the
  `help-common` target.
- A module Makefile must derive `MODULE_ROOT` and `MODULE` and define genuine
  module-specific exceptions before including the shared file:

  ```makefile
  MODULE_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
  MODULE ?= $(notdir $(MODULE_ROOT))
  IMAGE_REPOSITORY ?= service-image
  include ../mk/common.mk
  ```

- Keep module-specific deployment intent, Helm value keys, dependency update
  commands, smoke tests, and multi-component orchestration in the module file.
- Do not create a central dispatcher with conditionals for every module. Shared
  files should provide reusable mechanics, not hide deployment ordering.
- The module `help` target must invoke `$(MAKE) help-common` once, then print only
  module-specific targets and exceptions. This composes complete help output
  without repeating common text in every Makefile.

### Variable precedence and directory assumptions

- Make variables follow this practical precedence order:
  1. command-line assignment, for example `make IMAGE_TAG=1.2.3`
  2. imported shell environment assignment, for example `IMAGE_TAG=1.2.3 make`
  3. module Makefile assignment
  4. shared include default
- Use `?=` for defaults so module and command-line overrides remain possible.
- `MODULE` defaults to the module Makefile's directory basename. The convention
  assumes that directory name is a suitable Kubernetes/module identifier and,
  unless overridden, a suitable image repository and chart repository prefix.
- Override `MODULE`, `NAMESPACE`, `IMAGE_REPOSITORY`, `CHART_REPOSITORY`,
  `HELM_RELEASE`, or `HELM_CHART` in the module Makefile when that assumption is
  false, or on the command line for a one-off operation. Document those exceptions
  in the module README and in the module-specific portion of `help`.
- `MODULE_ROOT` must be computed before including a shared file; otherwise
  `$(lastword $(MAKEFILE_LIST))` may refer to the include rather than the module.

## Variable Conventions

### Images

- For the primary image, use the unprefixed variables `IMAGE_REGISTRY`,
  `IMAGE_REPOSITORY`, `IMAGE_TAG`, and derived `IMAGE`.
- Build `IMAGE` from its parts, omitting the registry when
  `IMAGE_REGISTRY` is empty. An empty registry should support resolving an image
  from a node's local image cache when the Helm values support that mode.
- Keep the primary artifact variables unprefixed so the same overrides can be
  reused unchanged across modules. Do not use `<SERVICE>_IMAGE_*` for the primary
  image.
- A full `IMAGE` override must be intentional and documented. If deployment uses
  split Helm image fields, either derive those fields from the full override or
  clearly document that `IMAGE` applies only to build/run targets. Do not advertise
  a full-image override that silently has no effect on `apply`.

### Charts

- Use unprefixed `CHART_REGISTRY`, `CHART_REPOSITORY`, and `CHART_VERSION` for the
  primary chart. Keep them independently overridable.
- Default `CHART_VERSION` to `IMAGE_TAG`; bump it explicitly when the chart changes
  without an application-image change.
- Default `CHART_REGISTRY` to `IMAGE_REGISTRY` and `CHART_REPOSITORY` to
  `IMAGE_REPOSITORY` with a `-chart` suffix. This keeps image and chart artifacts
  from colliding when published to the same registry and namespace.
- Leave the registry host/namespace empty by default (`CHART_REGISTRY ?=` chained
  from `IMAGE_REGISTRY ?=`) unless the component has an established default.
  Targets that need a registry must fail clearly when it is empty.
- Set `CHART_REPOSITORY` to the chart's `name:` in `helm/Chart.yaml`. OCI Helm
  push/pull references key off the chart name, so the normal convention is an image
  repository such as `hello-world` and chart name `hello-world-chart`.
- Derive `CHART_PACKAGE` in a gitignored distribution directory and
  `CHART_OCI_REF` from the registry and repository.
- If a module has several images or charts, keep the unprefixed set for the
  primary/default artifact when one is obvious. Add a distinguishing suffix only
  for additional artifacts, such as `WORKER_IMAGE_REPOSITORY` or
  `WORKER_CHART_VERSION`; do not prefix every artifact with the module name.
- Use unprefixed `HELM_RELEASE`, `HELM_CHART`, `HELM_VALUES_BASE`, and `HELM_VALUES`
  for a module's primary chart. Default the release to `MODULE` and the chart to
  `helm`; override them in the module file when the directory name does not match.
- For multiple independent charts, keep the primary chart unprefixed when there is
  an obvious primary and use distinguishing suffixes for additional charts, such
  as `AUTH_HELM_RELEASE` and `AUTH_HELM_CHART`. Do not prefix a single chart merely
  to encode the module name.

### Helm values and test inputs

- Define reusable values variables, such as `<SERVICE>_HELM_VALUES_BASE` and
  `<SERVICE>_HELM_VALUES`, rather than repeating `-f` arguments in recipes.
- Require explicit `ENV` for targets that depend on layered environment values
  files. Do not silently select an environment.
- Keep test inputs overridable, for example `SMOKE_NAME`, `SMOKE_PORT`, and
  `SERVICE_PORT`. Document relationships between them when an override must stay
  synchronized with a chart value.
- Define `HELM_EXTRA_ARGS ?=` and append it to render/apply commands so operators
  can pass extra Helm flags without changing the Makefile.

## Target Design

- Mark every workflow and helper target `.PHONY`.
- Use concise, action-oriented, kebab-cased names: `build-image`, `push-image`,
  `build-chart`, `push-chart`, `render`, `apply`, `reconcile`, `smoke`, `delete`,
  and `run-local` where applicable.
- Add CLI checks as prerequisites of every target that invokes the CLI. Keep small
  checks such as `check-kubectl`, `check-helm`, `check-docker`, and `check-skopeo`
  reusable; compose them in `check-tools` with `$(MAKE)`.
- Preserve staged operational ordering:
  - render/build before apply/install
  - base charts and secrets before optional feature toggles
  - route or live-update controllers before relying on dynamic route updates
  - reconcile before smoke validation
- Orchestrator targets must print numbered step banners (`[1/N]`) and a short reason
  line for each step.
- Keep baseline installation and optional feature toggles in separate targets.
- When renaming a commonly used target, retain a compatibility alias and print a
  deprecation notice from the alias.

## Cluster Safety and Kubernetes Practices

- Use `helm upgrade --install` for idempotent apply targets, normally with
  `--create-namespace` when the target owns the namespace lifecycle.
- Add a `confirm-target-cluster` prerequisite to apply, delete, and other
  destructive or in-cluster targets. Before asking for confirmation, display the
  current context, cluster, API server, Kubernetes user, and target namespace.
- Make confirmation default to interactive refusal unless the operator enters an
  explicit affirmative response.
- Support a cluster-operation `FORCE ?= false` (accepting the repository's chosen
  true forms) to bypass confirmation deliberately. Never reuse this flag for
  registry overwrite permission.
- Pass image registry, repository, and tag overrides to Helm with `--set-string` so
  values such as numeric-looking tags are not type-coerced.
- Give `apply` an explicit chart-source toggle, for example
  `CHART_SOURCE ?= registry`. Registry is the default and installs the published
  chart with `--version`; `CHART_SOURCE=local` installs from the working tree for
  local iteration and bypasses chart publication.
- In registry mode, fail fast if `CHART_REGISTRY` is empty and explain that the
  operator can set it or choose `CHART_SOURCE=local`.

## Image Lifecycle

- `build-image` must depend on `check-docker` and build from the module's tracked
  image source using the derived `IMAGE` reference.
- `push-image` must depend on `check-docker` and `check-skopeo`.
- Before pushing a versioned image, inspect `docker://$(IMAGE)` and fail if it
  already exists unless `IMAGE_PUSH_FORCE=true` (or the module's documented
  explicit true form) is set. The error must name the image and override flag.
- Use `skopeo inspect "docker://$(IMAGE)"`, not `docker manifest inspect` or
  `docker buildx imagetools inspect`. The repository's Podman-backed `docker`
  shim can reject OCI single-image manifests as manifest lists, making those checks
  falsely report a missing image; BuildKit/buildx is not available in the local
  tooling. `skopeo inspect` queries the registry directly and handles both OCI
  manifests and manifest lists.
- Do not use the cluster-operation `FORCE` flag to permit an image overwrite.

## Helm Chart Build and Push Lifecycle

- Mirror the image lifecycle with `build-chart` and `push-chart`.
- `build-chart` must lint the chart, create a gitignored distribution directory,
  remove stale packages for the same chart/version pattern, and run
  `helm package` with `--version $(CHART_VERSION)` and the application version
  where applicable.
- `push-chart` must depend on `build-chart` and `check-helm`, require a non-empty
  `CHART_REGISTRY`, and push the packaged `.tgz` to the OCI registry reference.
- Before pushing a versioned chart, run
  `helm show chart <oci-ref> --version <version>` and fail if that version exists
  unless `CHART_PUSH_FORCE=true` is explicitly set. Chart existence checks do not
  need `skopeo`.
- Keep `IMAGE_PUSH_FORCE` and `CHART_PUSH_FORCE` separate from `FORCE`; each
  controls only overwriting its own published artifact.

## Smoke and Local Validation

- Add `run-local` when the module can be exercised without Kubernetes. It should
  depend on `build-image`, publish the configured local port, pass required runtime
  environment values, and run the derived `IMAGE`.
- A Kubernetes `smoke` target should depend on `check-kubectl`, wait for rollout
  readiness, and validate through a temporary `kubectl port-forward`.
- Run the port-forward in the background, capture its PID, and install an EXIT
  trap that terminates it even when a later assertion fails.
- Wait for a health endpoint or equivalent readiness signal before making requests;
  do not rely only on a fixed delay.
- Assert at least one successful functional response and relevant failure behavior,
  such as a length-limit request returning the expected `400` status.
- Use `set -euo pipefail` in the multi-command smoke recipe and report concise,
  actionable failures.

## Publishing and Registry Guardrails

- Every `push-*` target publishing a versioned artifact must check for an existing
  version and fail by default with a clear force-flag message.
- Keep overwrite guards per artifact: `IMAGE_PUSH_FORCE` for images and
  `CHART_PUSH_FORCE` for charts. Never conflate them with cluster confirmation.
- Registry-dependent targets must validate registry variables before invoking the
  publish or registry-install command.

## Help and User Experience

- Keep `help` as the operator-facing index and update it whenever targets or
  variables are added, removed, or renamed.
- Group help output by primary flow, local development, teardown, diagnostics, and
  override variables.
- Show representative current variable values in help where useful, including
  image parts, chart settings, chart source, smoke inputs, extra Helm arguments,
  `FORCE`, and per-artifact push-force flags.
- Describe outcomes with concise `done:` statements. Explain important guardrails,
  such as an existing registry version causing a failure unless the corresponding
  force flag is enabled.
