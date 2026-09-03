from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable

import jwt
from fastapi import FastAPI, Header, Response, status

API_KEYS_FILE = Path(os.getenv("API_KEYS_FILE", "/secrets/api_keys.txt"))
JWT_SECRET_FILE = Path(os.getenv("JWT_SECRET_FILE", "/secrets/jwt_hs256_secret"))
RELOAD_INTERVAL_SEC = int(os.getenv("SECRET_RELOAD_INTERVAL_SEC", "5"))


class SecretState:
    def __init__(self) -> None:
        self._api_keys: set[str] = set()
        self._jwt_secret: str | None = None
        self._last_reload = 0.0

    @property
    def api_keys(self) -> set[str]:
        return self._api_keys

    @property
    def jwt_secret(self) -> str | None:
        return self._jwt_secret

    def _read_api_keys(self) -> set[str]:
        if not API_KEYS_FILE.exists():
            return set()
        with API_KEYS_FILE.open("r", encoding="utf-8") as fh:
            return {line.strip() for line in fh if line.strip()}

    def _read_jwt_secret(self) -> str | None:
        if not JWT_SECRET_FILE.exists():
            return None
        value = JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
        return value or None

    def refresh_if_needed(self) -> None:
        now = time.time()
        if now - self._last_reload < RELOAD_INTERVAL_SEC:
            return
        self._api_keys = self._read_api_keys()
        self._jwt_secret = self._read_jwt_secret()
        self._last_reload = now


state = SecretState()
app = FastAPI(title="lm-serve-auth-service", docs_url=None, redoc_url=None)


def _is_valid_api_key(api_key: str | None, valid_keys: Iterable[str]) -> bool:
    return bool(api_key and api_key.strip() in valid_keys)


def _is_valid_jwt(authz: str | None, secret: str | None) -> bool:
    if not authz or not secret:
        return False
    if not authz.lower().startswith("bearer "):
        return False

    token = authz.split(" ", 1)[1].strip()
    if not token:
        return False

    try:
        jwt.decode(token, secret, algorithms=["HS256"])
        return True
    except jwt.PyJWTError:
        return False


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/authorize")
def authorize(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> Response:
    state.refresh_if_needed()

    if _is_valid_api_key(x_api_key, state.api_keys):
        return Response(content="allow", status_code=status.HTTP_200_OK)

    if _is_valid_jwt(authorization, state.jwt_secret):
        return Response(content="allow", status_code=status.HTTP_200_OK)

    return Response(content="unauthorized", status_code=status.HTTP_401_UNAUTHORIZED)
