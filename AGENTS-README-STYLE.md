# AGENTS-README-STYLE.md

## Purpose

Define repository standards for authoring production-grade README files.

Use these instructions when creating or editing any README in this repository, including component READMEs under `lm-serve/*/` and operational guides at module root.

## Goals

- Make README files useful for first-time operators and production maintainers.
- Explain both "how to run" and "how the system works".
- Keep behavior, configuration, security, and operational risk explicit.
- Prefer decision-ready guidance over shallow feature lists.

## Required README Structure

Every production-facing README should include these sections (rename headings only when context demands it):

1. Overview
- What the component does.
- Who should use this document.
- One-paragraph summary of production intent.

2. Production Design Overview
- Explain major lifecycle stages end-to-end.
- Describe system boundaries and consumer/producer contracts.
- Call out failure boundaries and safety gates.

3. Mermaid Diagram(s)
- Include at least one `flowchart` or `sequenceDiagram` for non-trivial systems.
- Diagram nodes must include meaningful operator guidance, not only resource names.
- At each stage, include what happens and what users should know.
- Keep diagrams renderable in GitHub Markdown (simple syntax, no experimental features).

4. What To Configure
- Explicitly list required settings, optional settings, and mode switches.
- Document each option with:
  - What it controls
  - When to use it
  - Runtime impact
- Include concrete examples for each major mode.

5. How To Run
- Local run path (where applicable).
- In-cluster/production run path.
- Copy-pasteable command examples.
- Prefer Make target examples when available in repository workflows.

6. Security and Hardening
- Secret handling expectations.
- Identity and access scope guidance.
- Transport/network expectations (for example HTTPS, egress restrictions).
- Runtime hardening assumptions and limitations.

7. Operational Runbook
- Pre-deploy checklist.
- During-deploy checks and expected signals.
- Post-deploy validation.
- Rollback or recovery approach.

8. Cross-environment Notes
- Cross-cluster/cloud behavior if relevant.
- Environment-specific caveats and portability notes.

## Component Documentation Layout Pattern

For component folders (for example `lm-serve/auth`, `lm-serve/platform`, `lm-serve/models`, `lm-serve/publisher`), use a consistent three-layer README layout.

1. Component-level README
- Required file: `component/README.md`
- Purpose: provide the gist of the component and explain its role in the system.
- Must include:
  - short intro describing what the component does end-to-end
  - which parts are image-driven, helm-driven, or both
  - when image and helm paths can be used independently
  - links to all subfolder READMEs (`helm/README.md`, `image/README.md`, and any additional subfolder docs)
- Keep implementation detail light here; defer deep details to subfolder READMEs.

2. Helm-specific README
- Required file when Helm exists: `component/helm/README.md`
- Purpose: chart ownership, values, deployment modes, and helm operational guidance.
- Must assume it can be read independently from image docs.

3. Image-specific README
- Required file when image/runtime exists: `component/image/README.md`
- Purpose: runtime behavior, build/run, security and production operation for image-only use cases.
- Must assume it can be used outside Helm (for example CI pipelines, alternate orchestrators, or manual automation).

If a component has additional meaningful subfolders, each should include its own README and be linked from the component-level README.

## Non-Helm and Non-Image Subfolder Rule

Any meaningful component subfolder other than `helm/` and `image/` must follow the same documentation pattern.

Required pattern:

1. Subfolder README location
- Required file: `component/<subfolder>/README.md`

2. Subfolder README purpose
- Describe what that subfolder owns.
- Describe how it is used independently from other subfolders when applicable.
- Describe integration points with the rest of the component.

3. Minimum subfolder README sections
- Overview
- Ownership boundary (what it owns vs does not own)
- How to use/run
- Configuration surface (if any)
- Security/operational notes (if runtime or deployment-affecting)

4. Component README linkage
- `component/README.md` must link to every meaningful subfolder README, not just `helm/README.md` and `image/README.md`.
- Links should include a one-line description of when to read that subfolder doc.

Goal:
- Users should be able to start at `component/README.md` and discover all implementation surfaces consistently, regardless of subfolder naming.

## Independence And Boundary Guidance

When documenting components that include both chart and image artifacts:

- Explicitly describe boundaries:
  - what Helm owns (packaging/orchestration)
  - what image/runtime owns (execution logic)
- Explicitly state independent usage paths:
  - image without Helm
  - Helm-managed deployment with image references
- Keep shared concepts aligned across docs, but avoid duplicating all details in all places.
- Put production design depth in the artifact-specific README where the behavior is implemented.

## Documentation Depth Expectations

For production-oriented components, avoid minimal READMEs.

Required depth:
- Explain architecture and data flow, not only CLI flags.
- Explain why stages exist (for example staging-before-promotion).
- Explain expected failure behavior and safe retry patterns.
- Explain consumer impact of each major transition.

## Mermaid Authoring Rules

- Prefer top-down flow for lifecycle pipelines.
- Keep node labels concise but informative.
- Use multiline node text to include:
  - Stage name
  - Action
  - Operator note
- Validate Mermaid syntax after edits.
- Avoid cluttered diagrams; split into two diagrams when needed.

## Configuration Documentation Rules

When documenting any setting with multiple options, include:

1. Option list
2. Decision guidance (when to choose each option)
3. Behavior impact
4. Example snippet per option

For mutually exclusive options, state that explicitly and warn against mixed configuration.

## Command and Path Guidance

- Commands must be runnable from a clearly stated working directory.
- Prefer repository-consistent workflows (`make` targets) over ad hoc command sets when both exist.
- Keep paths aligned with current repository layout.
- Update README paths immediately when components are moved.

## Production Safety Guidance

README files must make the following explicit where applicable:

- What action is serving-impacting.
- What validations happen before serving-impacting actions.
- What should never be done in production.
- Which artifacts are audit-relevant and should be retained.

## Style Rules

- Use direct, operational language.
- Keep sections scan-friendly with short paragraphs and flat bullet lists.
- Avoid filler wording and generic marketing language.
- Prefer concrete examples over abstract statements.
- Keep terminology consistent with chart values, Make targets, and runtime logs.

## Quality Checklist Before Finalizing

1. README includes design + runbook, not only usage snippets.
2. At least one Mermaid diagram is present and syntactically valid.
3. Security section includes secret, IAM/access, and network guidance.
4. Config sections explain options, defaults, and behavioral impact.
5. Commands and paths reflect current repository structure.
6. Failure/retry and rollback behavior are documented where relevant.
7. Links and references resolve correctly from the README location.
8. Component README links to `helm/README.md` and `image/README.md` (when those folders exist).
9. Helm and image READMEs can be understood independently and clearly describe ownership boundaries.
10. Any additional meaningful subfolder has `README.md` and is linked from the component README with usage context.

## Scope

These rules apply across the repository unless a more specific AGENTS.md in a subdirectory overrides them.
