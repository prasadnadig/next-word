# AGENTS-MAKEFILE-STYLE.md

## Purpose

Capture repository conventions for authoring and maintaining Makefile workflows.

## Variable Conventions

- Keep user-overridable variables at the top using `?=` defaults.
- Group variables by concern:
  - core deployment defaults
  - container image overrides
  - Helm release/chart/value wiring
  - test inputs and optional flags
- Prefer derived variables for composed values (for example `*_IMAGE` from repository and tag parts).

## Target Design

- Use `.PHONY` for all workflow targets.
- Keep target names action-oriented and kebab-cased.
- Preserve staged workflow ordering for operational targets:
  - render/build
  - apply/install
  - reconcile
  - validate/smoke
- For orchestrator targets, print numbered step banners (`[1/N]`) with short reason lines.

## Guardrails

- Add tool checks (`check-*`) as prerequisites for targets that depend on those CLIs.
- Require explicit `ENV` for targets that depend on layered values files.
- Keep helper/validation targets reusable and composed through `$(MAKE)` invocations.

## Helm and Kubernetes Practices

- Use `helm upgrade --install` for idempotent apply targets.
- Keep baseline install and optional feature toggles in separate targets.
- Pass image overrides via `--set-string` to avoid unintended type coercion.
- Preserve deployment order when there are dependencies:
  - base charts and secrets before feature toggles
  - route/live-update controller before relying on dynamic route updates
  - reconciliation before smoke validation

## UX and Documentation

- Keep `help` target updated whenever targets or variables are added/renamed.
- In help text, use concise `done:` statements describing outcomes.
- When renaming a commonly used target, keep a compatibility alias and print a deprecation notice.
