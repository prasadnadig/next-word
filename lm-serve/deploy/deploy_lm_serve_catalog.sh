#!/usr/bin/env bash
# Reconciles a model catalog ConfigMap into a multi-model vLLM serving stack.
#
# Design choices:
# - One StatefulSet per model for stable per-replica PVC caches.
# - Shared Envoy edge (Service type LoadBalancer by default).
# - Envoy calls an internal auth service that supports API key or JWT auth.
# - Model catalog is the single source of truth for add/update/remove operations.

set -euo pipefail

readonly SCRIPT_NAME="$(basename "$0")"
readonly MANAGED_BY_LABEL="vllm-catalog-deployer"
readonly ENVOY_CONFIG_NAME="vllm-envoy-config"
readonly AUTH_CONFIG_NAME="lm-serve-auth-service-config"

NAMESPACE="lm-serve"
CATALOG_CONFIGMAP="lm-serve-model-catalog"
CATALOG_KEY="models.yaml"
EDGE_SERVICE_TYPE="LoadBalancer"
VLLM_IMAGE_DEFAULT="vllm/vllm-openai:v0.8.5"
INIT_SYNC_IMAGE="amazon/aws-cli:2.22.14"
AUTH_SECRET_NAME="lm-serve-auth-secrets"
S3_SECRET_NAME="lm-serve-object-storage-creds"
STORAGE_CLASS_NAME=""
WATCH_INTERVAL_SEC="0"
DRY_RUN="false"
KUBECONFIG_PATH=""

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fatal()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS]

Reconcile vLLM model-serving resources from a ConfigMap data key that contains
YAML model catalog content.

Required catalog schema (high-level):
  storage:
    bucket: <bucket-name>
    endpoint: <https://s3-endpoint>
    region: <region>
  models:
    - name: mistral-7b-instruct
      enabled: true
      storage:
        prefix: models/mistral-7b-instruct
      serving:
        replicas: 1
        gpuCount: 1
        pvcSize: 120Gi
        port: 8000
        tensorParallelSize: 1
        maxModelLen: 8192
        dtype: float16
        runtimeClassName: ""
        priorityClassName: ""
        serviceAccountName: ""
        nodeSelector: {}
        tolerations: []
        affinity: {}
        extraArgs: []

OPTIONS:
  -n, --namespace NAME            Namespace to deploy into (default: ${NAMESPACE})
      --catalog-configmap NAME    ConfigMap containing catalog (default: ${CATALOG_CONFIGMAP})
      --catalog-key KEY           Data key in ConfigMap (default: ${CATALOG_KEY})
      --edge-service-type TYPE    Envoy edge Service type (default: ${EDGE_SERVICE_TYPE})
                                  Allowed: LoadBalancer, NodePort, ClusterIP
      --vllm-image IMAGE          Default vLLM image (default: ${VLLM_IMAGE_DEFAULT})
      --init-sync-image IMAGE     Init container image for S3 sync (default: ${INIT_SYNC_IMAGE})
      --auth-secret NAME          Secret with API keys + JWT secret (default: ${AUTH_SECRET_NAME})
      --s3-secret NAME            Secret with S3 credentials (default: ${S3_SECRET_NAME})
      --storage-class NAME        StorageClass for model PVCs (default: cluster default)
      --watch-interval-sec N      Reconcile loop interval; 0 = run once (default: ${WATCH_INTERVAL_SEC})
      --kubeconfig PATH           Optional kubeconfig path
      --dry-run                   Print generated resources and kubectl actions
  -h, --help                      Show help

Auth secret format (${AUTH_SECRET_NAME}):
  - key: api_keys.txt       value: newline separated API keys
  - key: jwt_hs256_secret   value: HS256 secret string

S3 secret format (${S3_SECRET_NAME}):
  - key: access_key_id
  - key: secret_access_key

EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace) NAMESPACE="$2"; shift 2 ;;
      --catalog-configmap) CATALOG_CONFIGMAP="$2"; shift 2 ;;
      --catalog-key) CATALOG_KEY="$2"; shift 2 ;;
      --edge-service-type) EDGE_SERVICE_TYPE="$2"; shift 2 ;;
      --vllm-image) VLLM_IMAGE_DEFAULT="$2"; shift 2 ;;
      --init-sync-image) INIT_SYNC_IMAGE="$2"; shift 2 ;;
      --auth-secret) AUTH_SECRET_NAME="$2"; shift 2 ;;
      --s3-secret) S3_SECRET_NAME="$2"; shift 2 ;;
      --storage-class) STORAGE_CLASS_NAME="$2"; shift 2 ;;
      --watch-interval-sec) WATCH_INTERVAL_SEC="$2"; shift 2 ;;
      --kubeconfig) KUBECONFIG_PATH="$2"; shift 2 ;;
      --dry-run) DRY_RUN="true"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fatal "Unknown option: $1" ;;
    esac
  done
}

kflags() {
  [[ -n "${KUBECONFIG_PATH}" ]] && echo "--kubeconfig=${KUBECONFIG_PATH}" || true
}

run() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo -e "${YELLOW}[DRY-RUN]${NC} $*"
  else
    "$@"
  fi
}

run_apply() {
  local tmpfile="$1"
  local kf; kf="$(kflags)"
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo -e "${YELLOW}[DRY-RUN]${NC} kubectl ${kf} apply -f ${tmpfile}"
    sed 's/^/    /' "${tmpfile}"
  else
    # shellcheck disable=SC2086
    kubectl ${kf} apply -f "${tmpfile}"
  fi
}

require_tools() {
  command -v kubectl >/dev/null 2>&1 || fatal "kubectl is required"
  command -v yq >/dev/null 2>&1 || fatal "yq is required (https://github.com/mikefarah/yq)"
  command -v awk >/dev/null 2>&1 || fatal "awk is required"

  [[ "${EDGE_SERVICE_TYPE}" =~ ^(LoadBalancer|NodePort|ClusterIP)$ ]] || \
    fatal "--edge-service-type must be LoadBalancer, NodePort, or ClusterIP"
  [[ "${WATCH_INTERVAL_SEC}" =~ ^[0-9]+$ ]] || fatal "--watch-interval-sec must be a non-negative integer"
}

ensure_namespace() {
  local kf; kf="$(kflags)"
  # shellcheck disable=SC2086
  if ! kubectl ${kf} get namespace "${NAMESPACE}" >/dev/null 2>&1; then
    run kubectl ${kf} create namespace "${NAMESPACE}"
  fi
}

fetch_catalog_to_file() {
  local dst="$1"
  local kf; kf="$(kflags)"
  local escaped_key
  escaped_key="${CATALOG_KEY//./\\.}"

  # shellcheck disable=SC2086
  local payload
  payload=$(kubectl ${kf} -n "${NAMESPACE}" get configmap "${CATALOG_CONFIGMAP}" \
    -o "jsonpath={.data.${escaped_key}}" 2>/dev/null || true)

  [[ -n "${payload}" ]] || fatal "Could not read ${CATALOG_KEY} from ConfigMap ${CATALOG_CONFIGMAP} in namespace ${NAMESPACE}"

  printf '%s\n' "${payload}" > "${dst}"
}

sanitize_name() {
  local raw="$1"
  local out
  out="$(echo "${raw}" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g')"
  [[ -n "${out}" ]] || fatal "Model name '${raw}' cannot be converted into a Kubernetes-safe name"
  echo "${out}"
}

bool_from_catalog() {
  local v="$1"
  case "${v}" in
    true|"true"|yes|"yes"|1|"1") echo "true" ;;
    *) echo "false" ;;
  esac
}

render_auth_service_script() {
  cat <<'PYEOF'
#!/usr/bin/env python3
"""Small internal auth service for Envoy ext_authz.

Accepts requests when either condition is true:
1) Header x-api-key matches an allowed key in /secrets/api_keys.txt
2) Authorization: Bearer <jwt> validates with HS256 using /secrets/jwt_hs256_secret
"""

import base64
import hashlib
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Dict, List, Optional

API_KEYS_PATH = "/secrets/api_keys.txt"
JWT_SECRET_PATH = "/secrets/jwt_hs256_secret"


def _load_api_keys() -> List[str]:
    try:
      with open(API_KEYS_PATH, "r", encoding="utf-8") as f:
          return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
      return []


def _load_jwt_secret() -> Optional[bytes]:
    try:
      with open(JWT_SECRET_PATH, "r", encoding="utf-8") as f:
          value = f.read().strip()
      return value.encode("utf-8") if value else None
    except FileNotFoundError:
      return None


def _b64url_decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("utf-8"))


def _verify_hs256_jwt(token: str, secret: bytes) -> bool:
    parts = token.split(".")
    if len(parts) != 3:
        return False

    signing_input = f"{parts[0]}.{parts[1]}".encode("utf-8")
    try:
      signature = _b64url_decode(parts[2])
      payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception:
      return False

    expected = hmac.new(secret, signing_input, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return False

    exp = payload.get("exp")
    if exp is not None:
        try:
            if int(exp) < int(time.time()):
                return False
        except Exception:
            return False

    nbf = payload.get("nbf")
    if nbf is not None:
        try:
            if int(nbf) > int(time.time()):
                return False
        except Exception:
            return False

    return True


class AuthHandler(BaseHTTPRequestHandler):
    api_keys: List[str] = []
    jwt_secret: Optional[bytes] = None

    def do_POST(self) -> None:
        # Envoy ext_authz expects 200 for allow, 401/403 for deny.
        headers: Dict[str, str] = {k.lower(): v for k, v in self.headers.items()}

        api_key = headers.get("x-api-key", "").strip()
        if api_key and api_key in self.api_keys:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"allow")
            return

        authz = headers.get("authorization", "")
        if authz.lower().startswith("bearer ") and self.jwt_secret:
            token = authz.split(" ", 1)[1].strip()
            if token and _verify_hs256_jwt(token, self.jwt_secret):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"allow")
                return

        self.send_response(401)
        self.end_headers()
        self.wfile.write(b"unauthorized")

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    AuthHandler.api_keys = _load_api_keys()
    AuthHandler.jwt_secret = _load_jwt_secret()

    server = HTTPServer(("0.0.0.0", 8080), AuthHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
PYEOF
}

render_and_apply_auth_service() {
  local tmpdir="$1"
  local out="${tmpdir}/auth-service.yaml"
  local script_file="${tmpdir}/auth_service.py"

  render_auth_service_script > "${script_file}"

  cat > "${out}" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${AUTH_CONFIG_NAME}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: auth
    app.kubernetes.io/name: lm-serve-auth-service
data:
  auth_service.py: |
$(sed 's/^/    /' "${script_file}")
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: lm-serve-auth-service
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: auth
    app.kubernetes.io/name: lm-serve-auth-service
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: lm-serve-auth-service
  template:
    metadata:
      labels:
        app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
        app.kubernetes.io/component: auth
        app.kubernetes.io/name: lm-serve-auth-service
    spec:
      automountServiceAccountToken: false
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      containers:
      - name: auth
        image: python:3.12.12-slim-bookworm
        imagePullPolicy: IfNotPresent
        command: ["python", "/app/auth_service.py"]
        ports:
        - containerPort: 8080
          name: http
        volumeMounts:
        - name: auth-script
          mountPath: /app/auth_service.py
          subPath: auth_service.py
          readOnly: true
        - name: auth-secrets
          mountPath: /secrets
          readOnly: true
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: ["ALL"]
          readOnlyRootFilesystem: true
          runAsNonRoot: true
          runAsUser: 10001
          runAsGroup: 10001
        resources:
          requests:
            cpu: 50m
            memory: 64Mi
          limits:
            cpu: 250m
            memory: 256Mi
      volumes:
      - name: auth-script
        configMap:
          name: ${AUTH_CONFIG_NAME}
          defaultMode: 0444
      - name: auth-secrets
        secret:
          secretName: ${AUTH_SECRET_NAME}
---
apiVersion: v1
kind: Service
metadata:
  name: lm-serve-auth-service
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: auth
    app.kubernetes.io/name: lm-serve-auth-service
spec:
  selector:
    app.kubernetes.io/name: lm-serve-auth-service
  ports:
  - name: http
    port: 8080
    targetPort: 8080
EOF

  run_apply "${out}"
}

render_envoy_config() {
  local catalog_file="$1"
  local dst="$2"

  local routes=""
  local clusters=""
  local count=0

  while IFS= read -r model_name; do
    [[ -n "${model_name}" ]] || continue
    count=$((count + 1))
    local model_k8s
    model_k8s="$(sanitize_name "${model_name}")"

    routes+=$(cat <<EOF
                  - match:
                      prefix: "/m/${model_name}/"
                    route:
                      cluster: "vllm-${model_k8s}"
                      prefix_rewrite: "/"

EOF
)

    clusters+=$(cat <<EOF
          - name: "vllm-${model_k8s}"
            type: STRICT_DNS
            connect_timeout: 2s
            lb_policy: RING_HASH
            load_assignment:
              cluster_name: "vllm-${model_k8s}"
              endpoints:
                - lb_endpoints:
                    - endpoint:
                        address:
                          socket_address:
                            address: "vllm-${model_k8s}.${NAMESPACE}.svc.cluster.local"
                            port_value: 8000

EOF
)
  done < <(yq -r '.models[] | select(.enabled == true) | .name' "${catalog_file}")

  [[ "${count}" -gt 0 ]] || fatal "No enabled models in catalog. Nothing to route in Envoy."

  cat > "${dst}" <<EOF
static_resources:
  listeners:
    - name: ingress_listener
      address:
        socket_address:
          address: 0.0.0.0
          port_value: 8080
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: ingress_http
                route_config:
                  name: local_route
                  virtual_hosts:
                    - name: lm_serve_apps
                      domains: ["*"]
                      routes:
${routes}                        - match:
                            path: "/healthz"
                          direct_response:
                            status: 200
                            body:
                              inline_string: "ok"
                http_filters:
                  - name: envoy.filters.http.ext_authz
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz
                      transport_api_version: V3
                      failure_mode_allow: false
                      with_request_body:
                        max_request_bytes: 8192
                        allow_partial_message: true
                      http_service:
                        server_uri:
                          uri: "http://lm-serve-auth-service.${NAMESPACE}.svc.cluster.local:8080"
                          cluster: authz_cluster
                          timeout: 3s
                        path_prefix: "/authorize"
                        authorization_request:
                          allowed_headers:
                            patterns:
                              - exact: "authorization"
                              - exact: "x-api-key"
                  - name: envoy.filters.http.router
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router
  clusters:
    - name: authz_cluster
      type: STRICT_DNS
      connect_timeout: 2s
      load_assignment:
        cluster_name: authz_cluster
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address:
                      address: lm-serve-auth-service.${NAMESPACE}.svc.cluster.local
                      port_value: 8080

${clusters}admin:
  address:
    socket_address:
      address: 127.0.0.1
      port_value: 9901
EOF
}

render_and_apply_envoy() {
  local catalog_file="$1"
  local tmpdir="$2"

  local envoy_yaml="${tmpdir}/envoy.yaml"
  render_envoy_config "${catalog_file}" "${envoy_yaml}"

  local out="${tmpdir}/envoy-resources.yaml"
  cat > "${out}" <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${ENVOY_CONFIG_NAME}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: edge
    app.kubernetes.io/name: vllm-envoy
data:
  envoy.yaml: |
$(sed 's/^/    /' "${envoy_yaml}")
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-envoy
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: edge
    app.kubernetes.io/name: vllm-envoy
spec:
  replicas: 2
  selector:
    matchLabels:
      app.kubernetes.io/name: vllm-envoy
  template:
    metadata:
      labels:
        app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
        app.kubernetes.io/component: edge
        app.kubernetes.io/name: vllm-envoy
    spec:
      automountServiceAccountToken: false
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      containers:
      - name: envoy
        image: envoyproxy/envoy:v1.33.2
        imagePullPolicy: IfNotPresent
        args: ["-c", "/etc/envoy/envoy.yaml", "--service-cluster", "vllm-edge"]
        ports:
          - name: http
            containerPort: 8080
          - name: admin
            containerPort: 9901
        volumeMounts:
          - name: envoy-config
            mountPath: /etc/envoy
            readOnly: true
        readinessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 10
        livenessProbe:
          httpGet:
            path: /healthz
            port: 8080
          initialDelaySeconds: 15
          periodSeconds: 20
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: ["ALL"]
          readOnlyRootFilesystem: true
          runAsNonRoot: true
          runAsUser: 101
          runAsGroup: 101
        resources:
          requests:
            cpu: 200m
            memory: 256Mi
          limits:
            cpu: 1000m
            memory: 1Gi
      volumes:
      - name: envoy-config
        configMap:
          name: ${ENVOY_CONFIG_NAME}
          defaultMode: 0444
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-edge
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: edge
    app.kubernetes.io/name: vllm-envoy
spec:
  type: ${EDGE_SERVICE_TYPE}
  selector:
    app.kubernetes.io/name: vllm-envoy
  ports:
  - name: http
    port: 80
    targetPort: 8080
EOF

  run_apply "${out}"
}

render_extra_args_yaml() {
  local catalog_file="$1"
  local model_name="$2"

  yq -r ".models[] | select(.name == \"${model_name}\") | .serving.extraArgs[]?" "${catalog_file}" 2>/dev/null | \
    awk 'NF > 0 { printf "        - \"%s\"\n", $0 }'
}

render_model_scalar_field() {
  local catalog_file="$1"
  local model_name="$2"
  local value_path="$3"
  local field_name="$4"
  local indent_spaces="$5"

  local value
  value="$(yq -r ".models[] | select(.name == \"${model_name}\") | (${value_path} // \"\")" "${catalog_file}" 2>/dev/null || true)"

  if [[ -z "${value}" || "${value}" == "null" ]]; then
    return 0
  fi

  local pad
  pad="$(printf '%*s' "${indent_spaces}" '')"
  printf "%s%s: %s\n" "${pad}" "${field_name}" "${value}"
}

render_model_yaml_subtree() {
  local catalog_file="$1"
  local model_name="$2"
  local value_path="$3"
  local field_name="$4"
  local indent_spaces="$5"

  local subtree
  subtree="$(yq -oy ".models[] | select(.name == \"${model_name}\") | ${value_path}" "${catalog_file}" 2>/dev/null || true)"

  if [[ -z "${subtree}" || "${subtree}" == "null" || "${subtree}" == "{}" || "${subtree}" == "[]" ]]; then
    return 0
  fi

  local pad
  pad="$(printf '%*s' "${indent_spaces}" '')"
  printf "%s%s:\n" "${pad}" "${field_name}"
  echo "${subtree}" | sed "s/^/${pad}  /"
}

render_and_apply_model_statefulset() {
  local catalog_file="$1"
  local model_name="$2"
  local tmpdir="$3"

  local model_k8s
  model_k8s="$(sanitize_name "${model_name}")"

  local replicas gpu_count pvc_size port tensor_parallel max_model_len dtype image storage_prefix
  replicas="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.serving.replicas // 1)" "${catalog_file}")"
  gpu_count="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.serving.gpuCount // 1)" "${catalog_file}")"
  pvc_size="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.serving.pvcSize // \"100Gi\")" "${catalog_file}")"
  port="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.serving.port // 8000)" "${catalog_file}")"
  tensor_parallel="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.serving.tensorParallelSize // 1)" "${catalog_file}")"
  max_model_len="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.serving.maxModelLen // 8192)" "${catalog_file}")"
  dtype="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.serving.dtype // \"auto\")" "${catalog_file}")"
  image="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.serving.image // \"${VLLM_IMAGE_DEFAULT}\")" "${catalog_file}")"
  storage_prefix="$(yq -r ".models[] | select(.name == \"${model_name}\") | .storage.prefix" "${catalog_file}")"

  [[ -n "${storage_prefix}" && "${storage_prefix}" != "null" ]] || fatal "Model ${model_name} missing storage.prefix"

  local bucket endpoint region
  bucket="$(yq -r '.storage.bucket' "${catalog_file}")"
  endpoint="$(yq -r '.storage.endpoint' "${catalog_file}")"
  region="$(yq -r '.storage.region // "us-east-1"' "${catalog_file}")"

  [[ -n "${bucket}" && "${bucket}" != "null" ]] || fatal "Catalog storage.bucket must be set"
  [[ -n "${endpoint}" && "${endpoint}" != "null" ]] || fatal "Catalog storage.endpoint must be set"

  local storage_class_block=""
  if [[ -n "${STORAGE_CLASS_NAME}" ]]; then
    storage_class_block="  storageClassName: ${STORAGE_CLASS_NAME}"
  fi

  local extra_args
  extra_args="$(render_extra_args_yaml "${catalog_file}" "${model_name}")"

  local runtime_class_name_yaml
  runtime_class_name_yaml="$(render_model_scalar_field "${catalog_file}" "${model_name}" '.serving.runtimeClassName' 'runtimeClassName' 6)"

  local priority_class_name_yaml
  priority_class_name_yaml="$(render_model_scalar_field "${catalog_file}" "${model_name}" '.serving.priorityClassName' 'priorityClassName' 6)"

  local service_account_name_yaml
  service_account_name_yaml="$(render_model_scalar_field "${catalog_file}" "${model_name}" '.serving.serviceAccountName' 'serviceAccountName' 6)"

  local node_selector_yaml
  node_selector_yaml="$(render_model_yaml_subtree "${catalog_file}" "${model_name}" '.serving.nodeSelector' 'nodeSelector' 6)"

  local tolerations_yaml
  tolerations_yaml="$(render_model_yaml_subtree "${catalog_file}" "${model_name}" '.serving.tolerations' 'tolerations' 6)"

  local affinity_yaml
  affinity_yaml="$(render_model_yaml_subtree "${catalog_file}" "${model_name}" '.serving.affinity' 'affinity' 6)"

  local out="${tmpdir}/model-${model_k8s}.yaml"
  cat > "${out}" <<EOF
apiVersion: v1
kind: Service
metadata:
  name: vllm-${model_k8s}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: model-runtime
    app.kubernetes.io/name: vllm-${model_k8s}
    lm-serve/model-name: ${model_name}
spec:
  selector:
    app.kubernetes.io/name: vllm-${model_k8s}
  ports:
  - name: http
    port: 8000
    targetPort: ${port}
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: vllm-${model_k8s}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: model-runtime
    app.kubernetes.io/name: vllm-${model_k8s}
    lm-serve/model-name: ${model_name}
spec:
  replicas: ${replicas}
  serviceName: vllm-${model_k8s}
  podManagementPolicy: Parallel
  selector:
    matchLabels:
      app.kubernetes.io/name: vllm-${model_k8s}
  template:
    metadata:
      labels:
        app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
        app.kubernetes.io/component: model-runtime
        app.kubernetes.io/name: vllm-${model_k8s}
        lm-serve/model-name: ${model_name}
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "${port}"
        prometheus.io/path: "/metrics"
    spec:
      restartPolicy: Always
${runtime_class_name_yaml}
${priority_class_name_yaml}
${service_account_name_yaml}
${node_selector_yaml}
${tolerations_yaml}
${affinity_yaml}
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      initContainers:
      - name: model-sync
        image: ${INIT_SYNC_IMAGE}
        imagePullPolicy: IfNotPresent
        env:
        - name: AWS_ACCESS_KEY_ID
          valueFrom:
            secretKeyRef:
              name: ${S3_SECRET_NAME}
              key: access_key_id
        - name: AWS_SECRET_ACCESS_KEY
          valueFrom:
            secretKeyRef:
              name: ${S3_SECRET_NAME}
              key: secret_access_key
        - name: AWS_DEFAULT_REGION
          value: "${region}"
        - name: S3_ENDPOINT
          value: "${endpoint}"
        - name: BUCKET
          value: "${bucket}"
        - name: MODEL_PREFIX
          value: "${storage_prefix}"
        command: ["/bin/sh", "-c"]
        args:
        - |
          set -euo pipefail
          mkdir -p /models/current
          aws --endpoint-url "${endpoint}" s3 sync "s3://${bucket}/${storage_prefix%/}/current/" /models/current/ --no-progress
          aws --endpoint-url "${endpoint}" s3 cp "s3://${bucket}/${storage_prefix%/}/manifest.json" /models/manifest.json --no-progress || true
        volumeMounts:
        - name: model-cache
          mountPath: /models
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: ["ALL"]
          runAsNonRoot: true
          runAsUser: 1000
          runAsGroup: 1000
      containers:
      - name: vllm
        image: ${image}
        imagePullPolicy: IfNotPresent
        args:
        - "--host"
        - "0.0.0.0"
        - "--port"
        - "${port}"
        - "--model"
        - "/models/current"
        - "--served-model-name"
        - "${model_name}"
        - "--tensor-parallel-size"
        - "${tensor_parallel}"
        - "--max-model-len"
        - "${max_model_len}"
        - "--dtype"
        - "${dtype}"
${extra_args}
        ports:
        - containerPort: ${port}
          name: http
        readinessProbe:
          httpGet:
            path: /health
            port: ${port}
          initialDelaySeconds: 20
          periodSeconds: 15
          timeoutSeconds: 5
        livenessProbe:
          httpGet:
            path: /health
            port: ${port}
          initialDelaySeconds: 60
          periodSeconds: 30
          timeoutSeconds: 5
        resources:
          limits:
            nvidia.com/gpu: "${gpu_count}"
          requests:
            nvidia.com/gpu: "${gpu_count}"
        volumeMounts:
        - name: model-cache
          mountPath: /models
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: ["ALL"]
          runAsNonRoot: true
          runAsUser: 1000
          runAsGroup: 1000
  volumeClaimTemplates:
  - metadata:
      name: model-cache
      labels:
        app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
        app.kubernetes.io/component: model-runtime
    spec:
      accessModes: ["ReadWriteOnce"]
${storage_class_block}
      resources:
        requests:
          storage: ${pvc_size}
EOF

  run_apply "${out}"
}

cleanup_removed_models() {
  local desired_csv="$1"
  local kf; kf="$(kflags)"

  local existing
  # shellcheck disable=SC2086
  existing=$(kubectl ${kf} -n "${NAMESPACE}" get statefulsets \
    -l "app.kubernetes.io/managed-by=${MANAGED_BY_LABEL},app.kubernetes.io/component=model-runtime" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)

  while IFS= read -r sts_name; do
    [[ -n "${sts_name}" ]] || continue
    if ! grep -qx "${sts_name}" <<< "${desired_csv}"; then
      warn "Removing stale model runtime: ${sts_name}"
      if [[ "${DRY_RUN}" == "true" ]]; then
        echo -e "${YELLOW}[DRY-RUN]${NC} kubectl ${kf} -n ${NAMESPACE} delete statefulset ${sts_name}"
        echo -e "${YELLOW}[DRY-RUN]${NC} kubectl ${kf} -n ${NAMESPACE} delete service ${sts_name}"
      else
        # shellcheck disable=SC2086
        kubectl ${kf} -n "${NAMESPACE}" delete statefulset "${sts_name}" --ignore-not-found
        # shellcheck disable=SC2086
        kubectl ${kf} -n "${NAMESPACE}" delete service "${sts_name}" --ignore-not-found
      fi
    fi
  done <<< "${existing}"
}

reconcile_once() {
  local tmpdir
  tmpdir="$(mktemp -d)"
  trap 'rm -rf "${tmpdir}"' RETURN

  local catalog_file="${tmpdir}/catalog.yaml"
  fetch_catalog_to_file "${catalog_file}"

  local model_count
  model_count="$(yq -r '.models | length' "${catalog_file}")"
  [[ "${model_count}" =~ ^[0-9]+$ ]] || fatal "Catalog is invalid: models must be a list"

  local desired_names=""

  while IFS= read -r model_name; do
    [[ -n "${model_name}" ]] || continue

    local enabled
    enabled="$(yq -r ".models[] | select(.name == \"${model_name}\") | (.enabled // true)" "${catalog_file}")"
    if [[ "$(bool_from_catalog "${enabled}")" != "true" ]]; then
      continue
    fi

    render_and_apply_model_statefulset "${catalog_file}" "${model_name}" "${tmpdir}"

    local model_k8s
    model_k8s="$(sanitize_name "${model_name}")"
    desired_names+="vllm-${model_k8s}"$'\n'
  done < <(yq -r '.models[].name' "${catalog_file}")

  [[ -n "${desired_names}" ]] || fatal "No enabled models found in catalog"

  cleanup_removed_models "${desired_names}"
  render_and_apply_auth_service "${tmpdir}"
  render_and_apply_envoy "${catalog_file}" "${tmpdir}"

  success "Reconciliation completed for namespace ${NAMESPACE}"
}

main() {
  parse_args "$@"
  require_tools
  ensure_namespace

  info "Reconciling vLLM catalog from ConfigMap ${CATALOG_CONFIGMAP}/${CATALOG_KEY} in namespace ${NAMESPACE}"

  if [[ "${WATCH_INTERVAL_SEC}" == "0" ]]; then
    reconcile_once
    return 0
  fi

  info "Watch mode enabled: interval ${WATCH_INTERVAL_SEC}s"
  while true; do
    reconcile_once || warn "Reconcile attempt failed; retrying at next interval"
    sleep "${WATCH_INTERVAL_SEC}"
  done
}

main "$@"
