# Route Aggregator Image

This image runs the live route aggregator used by the platform chart when
`routeConfig.liveUpdate.enabled=true`.

## Build

```bash
docker build -t <REGISTRY>/lm-serve-route-aggregator:0.1.0 lm-serve/route-aggregator
```

## Push

```bash
docker push <REGISTRY>/lm-serve-route-aggregator:0.1.0
```

## Helm values

Set the platform chart values:

```yaml
routeConfig:
  liveUpdate:
    controller:
      image:
        repository: <REGISTRY>/lm-serve-route-aggregator
        tag: 0.1.0
        pullPolicy: IfNotPresent
```
