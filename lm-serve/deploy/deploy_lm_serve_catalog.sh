#!/usr/bin/env bash
# Reconciles a model catalog ConfigMap into model runtime resources.
#
# Design choices:
# - One StatefulSet per model for stable per-replica PVC caches.
# - Model catalog is the single source of truth for add/update/remove operations.
# - Auth and Envoy software are now owned by dedicated Helm charts.

set -euo pipefail

readonly SCRIPT_NAME="$(basename "$0")"
readonly MANAGED_BY_LABEL="vllm-catalog-deployer"

NAMESPACE="lm-serve"
CATALOG_CONFIGMAP="lm-serve-model-catalog"
CATALOG_KEY="models.yaml"
MODEL_SERVICE_TYPE="LoadBalancer"
VLLM_IMAGE_DEFAULT="vllm/vllm-openai:v0.8.5"
INIT_SYNC_IMAGE="amazon/aws-cli:2.22.14"
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
      --model-service-type TYPE   Model Service type (default: ${MODEL_SERVICE_TYPE})
                                  Allowed: LoadBalancer, NodePort, ClusterIP
      --vllm-image IMAGE          Default vLLM image (default: ${VLLM_IMAGE_DEFAULT})
      --init-sync-image IMAGE     Init container image for S3 sync (default: ${INIT_SYNC_IMAGE})
      --s3-secret NAME            Secret with S3 credentials (default: ${S3_SECRET_NAME})
      --storage-class NAME        StorageClass for model PVCs (default: cluster default)
      --watch-interval-sec N      Reconcile loop interval; 0 = run once (default: ${WATCH_INTERVAL_SEC})
      --kubeconfig PATH           Optional kubeconfig path
      --dry-run                   Print generated resources and kubectl actions
  -h, --help                      Show help

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
      --model-service-type) MODEL_SERVICE_TYPE="$2"; shift 2 ;;
      --vllm-image) VLLM_IMAGE_DEFAULT="$2"; shift 2 ;;
      --init-sync-image) INIT_SYNC_IMAGE="$2"; shift 2 ;;
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

  [[ "${MODEL_SERVICE_TYPE}" =~ ^(LoadBalancer|NodePort|ClusterIP)$ ]] || \
    fatal "--model-service-type must be LoadBalancer, NodePort, or ClusterIP"
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

kind: Service
metadata:
  name: vllm-edge
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: ${MANAGED_BY_LABEL}
    app.kubernetes.io/component: edge
    app.kubernetes.io/name: vllm-envoy
spec:
  type: ${MODEL_SERVICE_TYPE}
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
