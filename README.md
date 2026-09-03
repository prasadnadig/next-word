# next-word

Workspace for the LM Serve split-chart migration and related GPU bootstrap notes.

## Top-level layout

- [lm-serve/](lm-serve/) - Model-serving workspace with charts, scripts, and deployment docs.
- [gpu-operator/](gpu-operator/) - GPU Operator bootstrap notes and setup plan.

## LM Serve entry points

- [lm-serve/Makefile](lm-serve/Makefile) - primary workflow for rendering, installing, reconciling, and smoke testing.
- [lm-serve/deploy/README-install.md](lm-serve/deploy/README-install.md) - installation and operations guide.
- [lm-serve/deploy/README-inference-usage.md](lm-serve/deploy/README-inference-usage.md) - consumer usage guide.
- [lm-serve/helm/lm-serve/chart-decomposition-proposal.md](lm-serve/helm/lm-serve/chart-decomposition-proposal.md) - chart split proposal and ownership matrix.

## Chart set

- [lm-serve/helm/lm-serve-auth/](lm-serve/helm/lm-serve-auth/) - auth service chart.
- [lm-serve/helm/lm-serve-platform/](lm-serve/helm/lm-serve-platform/) - Envoy platform chart.
- [lm-serve/helm/lm-serve-models/](lm-serve/helm/lm-serve-models/) - model catalog, route contract, and reconciler chart.
- [lm-serve/helm/lm-serve-publisher/](lm-serve/helm/lm-serve-publisher/) - model publisher chart.

## Notes

- The old shared `helm/lm-serve/` chart directory is now archived notes and sample overlays only.
- Environment-specific model profiles live under `lm-serve/helm/lm-serve-models/`.
