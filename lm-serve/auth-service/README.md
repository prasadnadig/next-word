# Auth Service

This module provides the internal auth service used by Envoy ext_authz.

## Endpoints

- `GET /healthz` - liveness/readiness probe.
- `POST /authorize` - returns `200 allow` for valid API key or JWT, else `401 unauthorized`.

## Secrets contract

- API keys file: `/secrets/api_keys.txt`
- JWT secret file: `/secrets/jwt_hs256_secret`

Override paths with:

- `API_KEYS_FILE`
- `JWT_SECRET_FILE`
- `SECRET_RELOAD_INTERVAL_SEC`

## Local run

```bash
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Build image

```bash
docker build -t lm-serve-auth-service:0.1.0 .
```
