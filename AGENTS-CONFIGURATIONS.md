# Configurations Agent Instructions

## Purpose

This file defines repository-wide authoring rules for configuration-oriented files, especially Helm values files and operational documentation.

Use these rules whenever editing or creating:
- `values.yaml` and `values.<env>.yaml`
- `README.md` and other operational docs
- Config files such as YAML, JSON, TOML, INI, and similar formats

## Core Principle

Prefer self-explanatory configuration with embedded guidance so users can safely change settings without reading template internals first.

For every setting that has multiple valid choices, document:
1. What each choice means
2. When to use each choice
3. Behavior impact at runtime or deploy time
4. A concrete sample for each choice

## Commenting Standards By File Type

### YAML and Helm values files

Use detailed comments directly above relevant keys.

Required pattern for multi-option settings:
1. Short intent line for the setting group
2. Enumerated options (`- optionA`, `- optionB`)
3. Behavior impact notes
4. Commented sample blocks for each option
5. If options are mutually exclusive, state that explicitly

Example pattern:

```yaml
feature:
  # Controls how credentials are sourced.
  # - create: chart generates Secret from inline values.
  # - existingSecret: chart references pre-created Secret.
  # Behavior impact:
  # - create is easy for bootstrap/testing.
  # - existingSecret is preferred for production secret management.
  mode: create

  # MUTUALLY EXCLUSIVE CHOICE:
  # Option A: mode=create
  # Option B: mode=existingSecret

  # Option A example
  # mode: create
  # secretName: app-secrets
  # value: REPLACE_ME

  # Option B example
  # mode: existingSecret
  # secretName: prod-app-secrets
```

YAML-specific rules:
- Keep comments concise but explicit.
- Do not duplicate identical long explanations in many places; keep one canonical explanation in base values and lighter hints in overlays.
- Preserve valid YAML structure even in commented examples.
- Keep placeholders obviously non-production (for example `REPLACE_ME_*`).

### JSON files

JSON does not support comments.

When detailed guidance is needed:
1. Put explanation in adjacent documentation (`README.md` or `*.md` in same directory).
2. Provide complete sample objects for each mode in markdown code fences.
3. If schema exists, encode constraints in schema (`enum`, `oneOf`, `anyOf`, `required`, `dependentRequired`).

### TOML files

Use `#` comments above keys and sections.

Required approach:
- Explain option semantics above the field.
- Add commented alternative examples when multiple modes exist.
- Keep examples in valid TOML style.

### INI files

Use `;` or `#` comments consistently with existing file style.

Required approach:
- Explain each optional toggle in the owning section.
- Include alternative key/value examples in comments.
- Clearly mark defaults and production recommendations.

## README and Operational Docs Standards

When a chart or tool has mode-based configuration, include a dedicated quickstart section.

Recommended structure:
1. "Quickstart" heading for the config topic
2. One subsection per mode/option
3. "When to use" bullets
4. "Behavior impact" bullets
5. Copy-pastable config block
6. Validation notes (what fails fast, what is required)

For decision-heavy settings, add a decision matrix table with columns:
- Need
- Mode/Option
- Required fields
- Default assumptions
- Security/operational implications

## Helm-Specific Rules

For Helm charts in this repository:

1. Base values file is canonical
- Put full explanation and alternative examples in `values.yaml`.
- Keep environment overlays concise and only override what differs.

2. Enforce documentation promises with template validation
- If comments claim options are mutually exclusive, enforce via `fail` guards.
- If a mode requires specific fields, fail fast during `helm template`.

3. Keep mode behavior explicit
- Prefer clear mode fields (for example `mode: internal|external`) over implicit behavior.
- Document how host/namespace/scheme fields are interpreted by each mode.

4. Validate after edits
- Run `helm template` with defaults.
- Run at least one example for each major mode.
- Run at least one intentionally invalid combo to verify guardrails.

## Quality Checklist Before Finalizing

Use this checklist after config/doc updates:

1. Multi-option settings include comments and examples.
2. Mutually exclusive choices are explicitly documented.
3. Template validation exists for critical mutually exclusive or required fields.
4. README quickstart exists for non-obvious mode selection.
5. Placeholders do not look like real credentials.
6. Helm render succeeds for default and representative overlays.
7. Invalid combinations fail with clear error text.

## Style Guardrails

1. Favor clarity over brevity for config comments.
2. Use plain language, avoid ambiguous words like "normal" or "standard" without definition.
3. Keep terminology consistent across values, templates, and docs.
4. When behavior changes by environment, state exactly which field drives the change.
5. Never include real secrets in repository examples.

## Scope

These rules apply across the repository unless a more specific AGENTS.md in a subdirectory overrides them.
