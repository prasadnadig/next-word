# Platform Component

This component provides the edge serving layer for LM Serve. It includes:

- a Helm chart for Envoy edge deployment and authz integration
- a route-aggregator image that continuously merges model routes into edge config

Use the image docs when you want to run the route aggregator independently. Use the Helm docs when you want chart-managed rollout of edge + route update behavior.

## What This Component Does

- Exposes model endpoints via Envoy edge.
- Connects edge authorization to auth service contracts.
- Aggregates route sources across namespaces when live update is enabled.

## Documentation Map

- [Helm chart README](helm/README.md)
- [Image/runtime README](image/README.md)
