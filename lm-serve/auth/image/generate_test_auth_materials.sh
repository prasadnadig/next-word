#!/usr/bin/env bash
# Generates small-team bootstrap API keys and HS256 JWT materials for testing.

set -euo pipefail

TEAM_SIZE="3"
JWT_SUBJECT="team-user"
JWT_TTL_SECONDS="3600"

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

OPTIONS:
  --team-size N         Number of API keys to generate (default: ${TEAM_SIZE})
  --jwt-sub SUBJECT     JWT subject claim (default: ${JWT_SUBJECT})
  --jwt-ttl-sec N       JWT validity in seconds (default: ${JWT_TTL_SECONDS})
  -h, --help            Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --team-size) TEAM_SIZE="$2"; shift 2 ;;
    --jwt-sub) JWT_SUBJECT="$2"; shift 2 ;;
    --jwt-ttl-sec) JWT_TTL_SECONDS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

[[ "${TEAM_SIZE}" =~ ^[0-9]+$ ]] || { echo "--team-size must be an integer" >&2; exit 1; }
[[ "${JWT_TTL_SECONDS}" =~ ^[0-9]+$ ]] || { echo "--jwt-ttl-sec must be an integer" >&2; exit 1; }

JWT_SECRET="$(openssl rand -base64 48 | tr -d '\n')"

API_KEYS=()
for _ in $(seq 1 "${TEAM_SIZE}"); do
  API_KEYS+=("lm_serve_$(openssl rand -hex 24)")
done

now_epoch="$(date +%s)"
exp_epoch="$((now_epoch + JWT_TTL_SECONDS))"

TEST_JWT="$(python3 - <<PY
import base64
import hashlib
import hmac
import json

secret = ${JWT_SECRET@Q}.encode("utf-8")
header = {"alg": "HS256", "typ": "JWT"}
payload = {
  "sub": ${JWT_SUBJECT@Q},
  "iat": int(${now_epoch}),
  "exp": int(${exp_epoch}),
  "scope": "llm:test"
}

def b64(data):
  return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode("utf-8").rstrip("=")

h = b64(header)
p = b64(payload)
sig = hmac.new(secret, f"{h}.{p}".encode("utf-8"), hashlib.sha256).digest()
s = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
print(f"{h}.{p}.{s}")
PY
)"

printf "\nGenerated API keys:\n"
for key in "${API_KEYS[@]}"; do
  printf "- %s\n" "${key}"
done

printf "\nGenerated JWT secret:\n%s\n" "${JWT_SECRET}"
printf "\nSample test JWT (HS256):\n%s\n" "${TEST_JWT}"

printf "\nSuggested Secret update:\n"
printf "kubectl -n lm-serve create secret generic lm-serve-auth-secrets \\\n  --from-literal=jwt_hs256_secrets.txt='%s' \\\n  --from-literal=api_keys.txt='" "${JWT_SECRET}"
for key in "${API_KEYS[@]}"; do
  printf "%s\\n" "${key}"
done
printf "' --dry-run=client -o yaml | kubectl apply -f -\n"
