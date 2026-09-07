# Inference Service Usage Guide

This guide is for application developers and testers consuming the deployed service.

## Endpoint format

Envoy routes each model by URL prefix:

- `http://<VLLM_EDGE>/m/<ENV_SEGMENT>/<MODEL_NAME>/v1/chat/completions`
- `http://<VLLM_EDGE>/m/<ENV_SEGMENT>/<MODEL_NAME>/v1/completions`
- `http://<VLLM_EDGE>/m/<ENV_SEGMENT>/<MODEL_NAME>/v1/embeddings`

`<ENV_SEGMENT>` defaults to the namespace name and can be overridden with
`routeConfig.namespaceTenantSegmentMap`.

Example model names are those from `models.yaml` in the catalog ConfigMap.

## Authentication options

You can authenticate with either:

1. API key via `x-api-key` header.
2. JWT bearer token via `Authorization: Bearer <token>`.

Both are validated by the Envoy auth service.

## Health checks

- Edge health: `GET /healthz`
- Model runtime health is internal and checked by Kubernetes probes.

## Curl examples

API key example:

```bash
curl -sS "http://<VLLM_EDGE>/m/default/mistral-7b-instruct/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "x-api-key: <YOUR_API_KEY>" \
  -d '{
    "model": "mistral-7b-instruct",
    "messages": [
      {"role": "system", "content": "You are concise."},
      {"role": "user", "content": "Summarize Kubernetes StatefulSet benefits in one paragraph."}
    ],
    "temperature": 0.2
  }'
```

JWT example:

```bash
curl -sS "http://<VLLM_EDGE>/m/default/mistral-7b-instruct/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <YOUR_JWT>" \
  -d '{
    "model": "mistral-7b-instruct",
    "messages": [
      {"role": "user", "content": "What is tensor parallelism?"}
    ]
  }'
```

## OpenAI-compatible SDK usage

You can point compatible clients to the model-specific base path.

Python example:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://<VLLM_EDGE>/m/default/mistral-7b-instruct/v1",
    api_key="<YOUR_API_KEY>",
)

resp = client.chat.completions.create(
    model="mistral-7b-instruct",
    messages=[{"role": "user", "content": "hello"}],
)
print(resp.choices[0].message.content)
```

If using JWT, pass `Authorization` header using the SDK transport hooks or direct HTTP client.

## Request stickiness guidance

For initial deployment, treat service as stateless and do not force client stickiness.

If workload tests show measurable cache-locality gains, introduce hash-based affinity at Envoy using a stable key (for example API key ID or JWT subject) as a follow-up enhancement.

## Troubleshooting quick checks

- `401 unauthorized`: API key/JWT missing or invalid.
- `404` on model path: model not enabled in catalog or reconcile not run.
- `503` from edge: model pod not ready or unavailable.
- Long first-request latency: model cold start or cache refill in init container.

## Automated smoke test

The deploy module includes an automated tester that discovers the edge endpoint
and tests all enabled models from the catalog:

```bash
bash smoke_test_lm_serve_catalog.sh \
  --namespace lm-serve \
  --catalog-configmap lm-serve-model-catalog \
  --catalog-key models.yaml \
  --api-key "<YOUR_API_KEY>"
```

Add `--jwt-token` to test JWT auth in the same run.
