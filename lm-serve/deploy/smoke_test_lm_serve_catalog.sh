#!/usr/bin/env bash
# Smoke-tests all enabled models behind the Envoy edge service.
#
# For each enabled model in the catalog, this script sends a tiny OpenAI-style
# chat completion request using API key auth and/or JWT auth.

set -euo pipefail

NAMESPACE="lm-serve"
CATALOG_CONFIGMAP="lm-serve-model-catalog"
CATALOG_KEY="models.yaml"
EDGE_SERVICE="vllm-edge"
TIMEOUT_SEC="45"
API_KEY=""
JWT_TOKEN=""
PROMPT="Reply with exactly the word OK."
KUBECONFIG_PATH=""

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fatal()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

OPTIONS:
  -n, --namespace NAME           Namespace (default: ${NAMESPACE})
      --catalog-configmap NAME   Catalog ConfigMap (default: ${CATALOG_CONFIGMAP})
      --catalog-key KEY          Catalog data key (default: ${CATALOG_KEY})
      --edge-service NAME        Envoy edge service name (default: ${EDGE_SERVICE})
      --timeout-sec N            Per-request timeout (default: ${TIMEOUT_SEC})
      --api-key VALUE            API key for x-api-key header
      --jwt-token VALUE          JWT for Authorization header
      --prompt TEXT              Prompt for test request
      --kubeconfig PATH          Optional kubeconfig path
  -h, --help                     Show help

At least one of --api-key or --jwt-token is required.
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace) NAMESPACE="$2"; shift 2 ;;
      --catalog-configmap) CATALOG_CONFIGMAP="$2"; shift 2 ;;
      --catalog-key) CATALOG_KEY="$2"; shift 2 ;;
      --edge-service) EDGE_SERVICE="$2"; shift 2 ;;
      --timeout-sec) TIMEOUT_SEC="$2"; shift 2 ;;
      --api-key) API_KEY="$2"; shift 2 ;;
      --jwt-token) JWT_TOKEN="$2"; shift 2 ;;
      --prompt) PROMPT="$2"; shift 2 ;;
      --kubeconfig) KUBECONFIG_PATH="$2"; shift 2 ;;
      -h|--help) usage; exit 0 ;;
      *) fatal "Unknown option: $1" ;;
    esac
  done
}

kflags() {
  [[ -n "${KUBECONFIG_PATH}" ]] && echo "--kubeconfig=${KUBECONFIG_PATH}" || true
}

require_tools() {
  command -v kubectl >/dev/null 2>&1 || fatal "kubectl is required"
  command -v yq >/dev/null 2>&1 || fatal "yq is required"
  command -v curl >/dev/null 2>&1 || fatal "curl is required"

  [[ "${TIMEOUT_SEC}" =~ ^[0-9]+$ ]] || fatal "--timeout-sec must be a non-negative integer"

  if [[ -z "${API_KEY}" && -z "${JWT_TOKEN}" ]]; then
    fatal "Provide --api-key and/or --jwt-token"
  fi
}

fetch_catalog_to_file() {
  local dst="$1"
  local kf; kf="$(kflags)"
  local escaped_key
  escaped_key="${CATALOG_KEY//./\\.}"

  # shellcheck disable=SC2086
  local payload
  payload=$(kubectl ${kf} -n "${NAMESPACE}" get configmap "${CATALOG_CONFIGMAP}" -o "jsonpath={.data.${escaped_key}}" 2>/dev/null || true)

  [[ -n "${payload}" ]] || fatal "Could not read ${CATALOG_KEY} from ConfigMap ${CATALOG_CONFIGMAP}"
  printf '%s\n' "${payload}" > "${dst}"
}

discover_edge_host() {
  local kf; kf="$(kflags)"

  # shellcheck disable=SC2086
  local host ip
  host=$(kubectl ${kf} -n "${NAMESPACE}" get svc "${EDGE_SERVICE}" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
  # shellcheck disable=SC2086
  ip=$(kubectl ${kf} -n "${NAMESPACE}" get svc "${EDGE_SERVICE}" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)

  if [[ -n "${host}" ]]; then
    echo "${host}"
    return 0
  fi

  if [[ -n "${ip}" ]]; then
    echo "${ip}"
    return 0
  fi

  fatal "Could not determine edge endpoint from Service ${EDGE_SERVICE}. Is LoadBalancer ready?"
}

request_model() {
  local mode="$1"
  local edge_host="$2"
  local model_name="$3"

  local url="http://${edge_host}/m/${model_name}/v1/chat/completions"
  local response_file http_code
  response_file="$(mktemp)"

  local auth_header=()
  if [[ "${mode}" == "api-key" ]]; then
    auth_header=( -H "x-api-key: ${API_KEY}" )
  else
    auth_header=( -H "Authorization: Bearer ${JWT_TOKEN}" )
  fi

  http_code=$(curl -sS -o "${response_file}" -w "%{http_code}" --max-time "${TIMEOUT_SEC}" \
    -H "Content-Type: application/json" \
    "${auth_header[@]}" \
    -d "{\"model\":\"${model_name}\",\"messages\":[{\"role\":\"user\",\"content\":\"${PROMPT}\"}],\"max_tokens\":8,\"temperature\":0}" \
    "${url}" || true)

  if [[ "${http_code}" == "200" ]]; then
    success "${mode}: ${model_name} -> HTTP 200"
  else
    warn "${mode}: ${model_name} -> HTTP ${http_code}"
    warn "  Response: $(tr '\n' ' ' < "${response_file}" | cut -c1-240)"
    rm -f "${response_file}"
    return 1
  fi

  rm -f "${response_file}"
  return 0
}

main() {
  parse_args "$@"
  require_tools

  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' EXIT

  local catalog_file="${tmpdir}/catalog.yaml"
  fetch_catalog_to_file "${catalog_file}"

  local edge_host
  edge_host="$(discover_edge_host)"
  info "Using edge endpoint: ${edge_host}"

  local models
  models="$(yq -r '.models[] | select(.enabled == true) | .name' "${catalog_file}")"
  [[ -n "${models}" ]] || fatal "No enabled models found in catalog"

  local failures=0

  while IFS= read -r model_name; do
    [[ -n "${model_name}" ]] || continue

    if [[ -n "${API_KEY}" ]]; then
      request_model "api-key" "${edge_host}" "${model_name}" || failures=$((failures + 1))
    fi

    if [[ -n "${JWT_TOKEN}" ]]; then
      request_model "jwt" "${edge_host}" "${model_name}" || failures=$((failures + 1))
    fi
  done <<< "${models}"

  if (( failures > 0 )); then
    fatal "Smoke test completed with ${failures} failure(s)"
  fi

  success "Smoke test passed for all enabled models and provided auth modes"
}

main "$@"
