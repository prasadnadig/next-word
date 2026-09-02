#!/usr/bin/env bash
# Bootstraps the NVIDIA GPU Operator onto a Linode LKE cluster (Phase 1).
# Installs the GPU Operator via Helm, which in turn manages: NVIDIA kernel drivers,
# the container toolkit (containerd hook), the device plugin (nvidia.com/gpu resource),
# DCGM Exporter (metrics), and Node Feature Discovery (hardware labels).
# After install, validates that GPUs are visible as schedulable resources.

set -euo pipefail

# ---------------------------------------------------------------------------
# Pinned versions — bump deliberately; do not auto-upgrade in CI
# ---------------------------------------------------------------------------
readonly DEFAULT_GPU_OPERATOR_VERSION="v24.9.2"
readonly DEFAULT_CUDA_TEST_IMAGE="nvidia/cuda:12.4.0-base-ubuntu22.04"
readonly HELM_REPO_NAME="nvidia"
readonly HELM_REPO_URL="https://helm.ngc.nvidia.com/nvidia"
readonly HELM_CHART="nvidia/gpu-operator"

# ---------------------------------------------------------------------------
# Defaults for all tuneable options
# ---------------------------------------------------------------------------
GPU_OPERATOR_VERSION="${DEFAULT_GPU_OPERATOR_VERSION}"
NAMESPACE="gpu-operator"
RELEASE_NAME="gpu-operator"
DRIVER_ENABLED="true"         # set false if nodes already have NVIDIA drivers
TOOLKIT_ENABLED="true"        # containerd runtime hook
DEVICE_PLUGIN_ENABLED="true"  # exposes nvidia.com/gpu to the scheduler
DCGM_ENABLED="true"           # GPU metrics for Prometheus/Grafana
WAIT_TIMEOUT="600s"           # helm --wait timeout
DRY_RUN="false"               # print commands, do not execute
SKIP_VALIDATION="false"       # skip post-install GPU visibility check
KUBECONFIG_PATH=""            # empty = use current KUBECONFIG / default context
NODE_LABEL_SELECTOR="nvidia.com/gpu.present=true" # selector for the validation pod
VALIDATION_ONLY="false"       # run only steps 3 and 4 (plus pre-flight)
WAIT_FOR_DAEMONSETS="true"    # wait for GPU Operator DaemonSets before validation
DS_WAIT_TIMEOUT_SECONDS="900" # max wait window for daemonset readiness
DS_WAIT_INTERVAL_SECONDS="15" # poll interval for daemonset readiness checks

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fatal()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Installs the NVIDIA GPU Operator on a Kubernetes cluster using Helm.
This is Phase 1 of the GPU LKE cluster bootstrap:
  - Adds the NVIDIA Helm repository
  - Installs the GPU Operator chart (drivers, toolkit, device plugin, DCGM, NFD)
  - Validates that nvidia.com/gpu is visible as a schedulable resource
  - Optionally runs a smoke-test pod (nvidia-smi) to confirm end-to-end GPU access

Target: Linode LKE with NVIDIA RTX 4000 Ada nodes (Linode plan g2-gpu-rtx4000a1-m).
Refer to gpu-cluster-bootstrap-plan.md for the full multi-phase plan.

OPTIONS:
  -n, --namespace NAME            Kubernetes namespace for the GPU Operator
                                  (default: ${NAMESPACE})
  -r, --release-name NAME         Helm release name  (default: ${RELEASE_NAME})
  -v, --version VERSION           GPU Operator chart version to install
                                  (default: ${DEFAULT_GPU_OPERATOR_VERSION})
                                  Find available versions:
                                    helm search repo nvidia/gpu-operator --versions
      --no-driver                 Skip NVIDIA driver installation (use when nodes
                                  already have drivers pre-installed by the OS/LKE)
      --no-toolkit                Skip NVIDIA Container Toolkit installation
      --no-device-plugin          Skip the k8s Device Plugin (nvidia.com/gpu won't
                                  be schedulable — only useful for testing)
      --no-dcgm                   Skip DCGM Exporter (disables GPU metrics)
  -t, --wait-timeout DURATION     Helm --wait timeout  (default: ${WAIT_TIMEOUT})
      --kubeconfig PATH           Path to kubeconfig file; overrides KUBECONFIG env
      --skip-validation           Skip post-install GPU visibility validation
      --dry-run                   Print all commands without executing them
      --validation-only           Run only validation steps (3 and 4); skip install
      --no-wait-for-daemonsets    Skip waiting for GPU Operator DaemonSets and
                                  run validation immediately
      --ds-wait-timeout-sec N     Wait window in seconds for daemonset readiness
                                  (default: ${DS_WAIT_TIMEOUT_SECONDS})
      --ds-wait-interval-sec N    Poll interval in seconds while waiting
                                  (default: ${DS_WAIT_INTERVAL_SECONDS})
  -h, --help                      Show this help message and exit

EXAMPLES:
  # Standard install with all components (recommended):
  $(basename "$0")

  # Install a specific version:
  $(basename "$0") --version v24.9.2

  # Nodes already have drivers (e.g. custom base image with NVIDIA driver):
  $(basename "$0") --no-driver

  # Dry-run to preview commands:
  $(basename "$0") --dry-run

  # Custom namespace and kubeconfig:
  $(basename "$0") -n gpu-system --kubeconfig ~/.kube/lke-gpu.yaml

  # Validation-only mode (when daemonsets are still initialising):
  $(basename "$0") --validation-only
  $(basename "$0") --validation-only --ds-wait-timeout-sec 1800 --ds-wait-interval-sec 20

  # Validation-only mode, but do not wait for daemonsets:
  $(basename "$0") --validation-only --no-wait-for-daemonsets

  # Full install + validation without daemonset wait loop:
  $(basename "$0") --no-wait-for-daemonsets

PINNED VERSIONS (change only intentionally):
  GPU Operator : ${DEFAULT_GPU_OPERATOR_VERSION}
  CUDA test img: ${DEFAULT_CUDA_TEST_IMAGE}

EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -n|--namespace)       NAMESPACE="$2";             shift 2 ;;
      -r|--release-name)    RELEASE_NAME="$2";          shift 2 ;;
      -v|--version)         GPU_OPERATOR_VERSION="$2";  shift 2 ;;
      --no-driver)          DRIVER_ENABLED="false";     shift   ;;
      --no-toolkit)         TOOLKIT_ENABLED="false";    shift   ;;
      --no-device-plugin)   DEVICE_PLUGIN_ENABLED="false"; shift ;;
      --no-dcgm)            DCGM_ENABLED="false";       shift   ;;
      -t|--wait-timeout)    WAIT_TIMEOUT="$2";          shift 2 ;;
      --kubeconfig)         KUBECONFIG_PATH="$2";       shift 2 ;;
      --skip-validation)    SKIP_VALIDATION="true";     shift   ;;
      --dry-run)            DRY_RUN="true";             shift   ;;
      --validation-only)    VALIDATION_ONLY="true";     shift   ;;
      --no-wait-for-daemonsets) WAIT_FOR_DAEMONSETS="false"; shift ;;
      --ds-wait-timeout-sec) DS_WAIT_TIMEOUT_SECONDS="$2"; shift 2 ;;
      --ds-wait-interval-sec) DS_WAIT_INTERVAL_SECONDS="$2"; shift 2 ;;
      -h|--help)            usage; exit 0 ;;
      *) fatal "Unknown option: $1. Run with --help for usage." ;;
    esac
  done
}

# ---------------------------------------------------------------------------
# Validate numeric args
# ---------------------------------------------------------------------------
validate_args() {
  [[ "${DS_WAIT_TIMEOUT_SECONDS}" =~ ^[0-9]+$ ]] || fatal "--ds-wait-timeout-sec must be a non-negative integer"
  [[ "${DS_WAIT_INTERVAL_SECONDS}" =~ ^[0-9]+$ ]] || fatal "--ds-wait-interval-sec must be a positive integer"
  [[ "${DS_WAIT_INTERVAL_SECONDS}" -gt 0 ]] || fatal "--ds-wait-interval-sec must be > 0"

  if [[ "${VALIDATION_ONLY}" == "true" && "${SKIP_VALIDATION}" == "true" ]]; then
    fatal "Cannot combine --validation-only with --skip-validation"
  fi
}

# ---------------------------------------------------------------------------
# Execute or print a command depending on --dry-run
# ---------------------------------------------------------------------------
run() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    echo -e "${YELLOW}[DRY-RUN]${NC} $*"
  else
    "$@"
  fi
}

# ---------------------------------------------------------------------------
# Build the common kubectl / helm flags (kubeconfig override if set)
# ---------------------------------------------------------------------------
kubectl_flags() {
  [[ -n "${KUBECONFIG_PATH}" ]] && echo "--kubeconfig=${KUBECONFIG_PATH}" || true
}

helm_flags() {
  [[ -n "${KUBECONFIG_PATH}" ]] && echo "--kubeconfig=${KUBECONFIG_PATH}" || true
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
preflight() {
  info "Running pre-flight checks..."

  command -v kubectl >/dev/null 2>&1 || fatal "kubectl not found in PATH"
  command -v helm    >/dev/null 2>&1 || fatal "helm not found in PATH"

  # Verify cluster connectivity
  local kflag; kflag=$(kubectl_flags)
  # shellcheck disable=SC2086
  if ! kubectl ${kflag} cluster-info --request-timeout=10s >/dev/null 2>&1; then
    fatal "Cannot reach the Kubernetes API server. Check your kubeconfig / context."
  fi

  local context
  # shellcheck disable=SC2086
  context=$(kubectl ${kflag} config current-context 2>/dev/null || echo "unknown")
  info "Active kube context: ${context}"

  # Warn if the GPU Operator release already exists (upgrade path, not fresh install)
  local hflag; hflag=$(helm_flags)
  # shellcheck disable=SC2086
  if helm ${hflag} status "${RELEASE_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    if [[ "${VALIDATION_ONLY}" == "true" ]]; then
      info "Helm release '${RELEASE_NAME}' already exists in namespace '${NAMESPACE}'."
      info "Validation-only mode: no Helm upgrade/install steps will run."
    else
      warn "Helm release '${RELEASE_NAME}' already exists in namespace '${NAMESPACE}'."
      warn "This script will upgrade it. Use 'helm rollback' to revert if needed."
    fi
  fi

  success "Pre-flight checks passed."
}

# ---------------------------------------------------------------------------
# Step 1 — Add / update the NVIDIA Helm repository
# ---------------------------------------------------------------------------
step_add_helm_repo() {
  info "Step 1/4 — Adding NVIDIA Helm repository (${HELM_REPO_URL})..."

  local hflag; hflag=$(helm_flags)

  # Add repo only if it isn't already registered under the expected name
  # shellcheck disable=SC2086
  if helm ${hflag} repo list 2>/dev/null | awk '{print $1}' | grep -qx "${HELM_REPO_NAME}"; then
    info "Helm repo '${HELM_REPO_NAME}' already registered; skipping add."
  else
    # shellcheck disable=SC2086
    run helm ${hflag} repo add "${HELM_REPO_NAME}" "${HELM_REPO_URL}"
  fi

  # Always update to pull latest chart index
  # shellcheck disable=SC2086
  run helm ${hflag} repo update "${HELM_REPO_NAME}"

  success "Helm repo ready."
}

# ---------------------------------------------------------------------------
# Step 2 — Install / upgrade GPU Operator
# ---------------------------------------------------------------------------
step_install_gpu_operator() {
  info "Step 2/4 — Installing GPU Operator ${GPU_OPERATOR_VERSION} into namespace '${NAMESPACE}'..."
  info "  driver.enabled        = ${DRIVER_ENABLED}"
  info "  toolkit.enabled       = ${TOOLKIT_ENABLED}"
  info "  devicePlugin.enabled  = ${DEVICE_PLUGIN_ENABLED}"
  info "  dcgm.enabled          = ${DCGM_ENABLED}"

  local hflag; hflag=$(helm_flags)

  # Use helm upgrade --install so the same command is idempotent (works for both
  # fresh installs and upgrades without branching logic here).
  # shellcheck disable=SC2086
  run helm ${hflag} upgrade --install "${RELEASE_NAME}" "${HELM_CHART}" \
    --version "${GPU_OPERATOR_VERSION}" \
    --namespace "${NAMESPACE}" \
    --create-namespace \
    --set driver.enabled="${DRIVER_ENABLED}" \
    --set toolkit.enabled="${TOOLKIT_ENABLED}" \
    --set devicePlugin.enabled="${DEVICE_PLUGIN_ENABLED}" \
    --set dcgm.enabled="${DCGM_ENABLED}" \
    --wait \
    --timeout "${WAIT_TIMEOUT}"

  success "GPU Operator deployed."
}

# ---------------------------------------------------------------------------
# Wait for expected GPU Operator DaemonSets to become ready
# ---------------------------------------------------------------------------
# NOTE ABOUT DRIVER DAEMONSET READINESS:
# The NVIDIA driver must exist on GPU nodes, but it does not always need to be
# installed by the GPU Operator driver DaemonSet.
#
# Two common modes:
# 1) Operator-managed driver: driver DaemonSet schedules pods and builds/loads
#    the kernel module on each matching node.
# 2) Host-managed driver: cloud image/OS/bootstrap already provides the driver,
#    so the operator may keep the driver DaemonSet present with DESIRED=0.
#
# In mode (2), DESIRED=0 is expected and should not block validation. What
# matters is whether nodes expose nvidia.com/gpu and workload smoke tests pass.
step_wait_for_daemonsets() {
  [[ "${WAIT_FOR_DAEMONSETS}" == "true" ]] || {
    info "Skipping DaemonSet readiness wait (--no-wait-for-daemonsets passed)."
    return 0
  }

  info "Step 2.5/4 — Waiting for GPU Operator DaemonSets to initialize..."
  info "  timeout: ${DS_WAIT_TIMEOUT_SECONDS}s, interval: ${DS_WAIT_INTERVAL_SECONDS}s"

  local expected_ds=()
  [[ "${DRIVER_ENABLED}" == "true" ]] && expected_ds+=("nvidia-driver-daemonset")
  [[ "${TOOLKIT_ENABLED}" == "true" ]] && expected_ds+=("nvidia-container-toolkit-daemonset")
  [[ "${DEVICE_PLUGIN_ENABLED}" == "true" ]] && expected_ds+=("nvidia-device-plugin-daemonset")
  [[ "${DCGM_ENABLED}" == "true" ]] && expected_ds+=("nvidia-dcgm-exporter")

  if [[ ${#expected_ds[@]} -eq 0 ]]; then
    info "No GPU Operator daemonsets expected with current flags; skipping wait."
    return 0
  fi

  local kflag; kflag=$(kubectl_flags)
  local start_ts now elapsed
  start_ts=$(date +%s)

  while true; do
    local all_ready="true"

    for ds in "${expected_ds[@]}"; do
      local line desired ready
      # shellcheck disable=SC2086
      line=$(kubectl ${kflag} -n "${NAMESPACE}" get daemonset "${ds}" --no-headers 2>/dev/null || true)

      if [[ -z "${line}" ]]; then
        all_ready="false"
        info "  waiting: ${ds} not found yet"
        continue
      fi

      desired=$(awk '{print $2}' <<< "${line}")
      ready=$(awk '{print $4}' <<< "${line}")

      if [[ ! "${desired}" =~ ^[0-9]+$ || ! "${ready}" =~ ^[0-9]+$ ]]; then
        all_ready="false"
        info "  waiting: ${ds} ready=${ready} desired=${desired}"
      elif [[ "${desired}" -eq 0 ]]; then
        info "  skipping: ${ds} desired=0 (component disabled in current release config)"
      elif [[ "${ready}" -lt "${desired}" ]]; then
        all_ready="false"
        info "  waiting: ${ds} ready=${ready} desired=${desired}"
      fi
    done

    if [[ "${all_ready}" == "true" ]]; then
      success "Expected GPU Operator DaemonSets are ready."
      return 0
    fi

    now=$(date +%s)
    elapsed=$((now - start_ts))
    if (( elapsed >= DS_WAIT_TIMEOUT_SECONDS )); then
      warn "Timed out after ${DS_WAIT_TIMEOUT_SECONDS}s waiting for DaemonSets. Continuing to validation."
      warn "Current daemonset state:"
      # shellcheck disable=SC2086
      kubectl ${kflag} -n "${NAMESPACE}" get daemonsets || true
      return 0
    fi

    info "  retrying in ${DS_WAIT_INTERVAL_SECONDS}s (elapsed ${elapsed}s / ${DS_WAIT_TIMEOUT_SECONDS}s)"
    sleep "${DS_WAIT_INTERVAL_SECONDS}"
  done
}

# ---------------------------------------------------------------------------
# Step 3 — Validate: nvidia.com/gpu appears in node allocatable resources
# ---------------------------------------------------------------------------
step_validate_gpu_resource() {
  info "Step 3/4 — Validating nvidia.com/gpu is allocatable on cluster nodes..."

  local kflag; kflag=$(kubectl_flags)

  # Print a human-readable table of GPU allocatable counts per node
  # shellcheck disable=SC2086
  local node_gpu_info
  node_gpu_info=$(kubectl ${kflag} get nodes \
    -o custom-columns="NODE:.metadata.name,GPU-ALLOCATABLE:.status.allocatable.nvidia\.com/gpu" \
    2>/dev/null)

  echo "${node_gpu_info}"

  # Count nodes with a numeric GPU allocatable value greater than zero.
  # This avoids false negatives from whitespace or non-numeric placeholders.
  local gpu_nodes_with_allocatable
  gpu_nodes_with_allocatable=$(echo "${node_gpu_info}" | awk '
    NR > 1 {
      if ($2 ~ /^[0-9]+$/ && $2 > 0) {
        count++
      }
    }
    END { print count + 0 }
  ')

  if [[ "${gpu_nodes_with_allocatable}" =~ ^[0-9]+$ ]] && (( gpu_nodes_with_allocatable > 0 )); then
    success "${gpu_nodes_with_allocatable} node(s) report nvidia.com/gpu allocatable > 0."
  else
    warn "No node shows nvidia.com/gpu yet. The GPU Operator DaemonSet pods may still be initialising."
    warn "Re-run validation manually:"
    warn "  kubectl ${kflag} get nodes -o custom-columns=NODE:.metadata.name,GPU:.status.allocatable.\"nvidia\\.com/gpu\""
    warn "  kubectl ${kflag} -n ${NAMESPACE} get pods"
  fi
}

# ---------------------------------------------------------------------------
# Step 4 — Smoke test: run nvidia-smi in a temporary pod
# ---------------------------------------------------------------------------
step_smoke_test() {
  info "Step 4/4 — Running nvidia-smi smoke test pod (will be deleted after)..."

  local kflag; kflag=$(kubectl_flags)
  local pod_name="gpu-smoke-test-$$"
  local smoke_timeout="180s"

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo -e "${YELLOW}[DRY-RUN]${NC} kubectl ${kflag} -n ${NAMESPACE} apply -f - <<EOF"
    cat <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: ${NAMESPACE}
spec:
  restartPolicy: Never
  containers:
  - name: nvidia-smi
    image: ${DEFAULT_CUDA_TEST_IMAGE}
    command: ["nvidia-smi"]
    resources:
      limits:
        nvidia.com/gpu: "1"
      requests:
        nvidia.com/gpu: "1"
EOF
    echo "EOF"
    echo -e "${YELLOW}[DRY-RUN]${NC} kubectl ${kflag} -n ${NAMESPACE} wait --for=jsonpath='{.status.phase}'=Succeeded pod/${pod_name} --timeout=${smoke_timeout}"
    echo -e "${YELLOW}[DRY-RUN]${NC} kubectl ${kflag} -n ${NAMESPACE} logs ${pod_name}"
    echo -e "${YELLOW}[DRY-RUN]${NC} kubectl ${kflag} -n ${NAMESPACE} delete pod ${pod_name} --ignore-not-found --wait=false"
    return
  fi

  # Create the pod from a manifest so GPU resource requests work across kubectl versions.
  # shellcheck disable=SC2086
  cat <<EOF | kubectl ${kflag} -n "${NAMESPACE}" apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata:
  name: ${pod_name}
  namespace: ${NAMESPACE}
spec:
  restartPolicy: Never
  containers:
  - name: nvidia-smi
    image: ${DEFAULT_CUDA_TEST_IMAGE}
    command: ["nvidia-smi"]
    resources:
      limits:
        nvidia.com/gpu: "1"
      requests:
        nvidia.com/gpu: "1"
EOF

  # shellcheck disable=SC2086
  if kubectl ${kflag} -n "${NAMESPACE}" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/${pod_name}" --timeout="${smoke_timeout}" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    kubectl ${kflag} -n "${NAMESPACE}" logs "${pod_name}" || true
    success "Smoke test passed — nvidia-smi ran successfully inside the cluster."
  else
    warn "Smoke test pod failed or timed out. Capturing quick diagnostics:"
    # shellcheck disable=SC2086
    kubectl ${kflag} -n "${NAMESPACE}" get pod "${pod_name}" -o wide || true
    # shellcheck disable=SC2086
    kubectl ${kflag} -n "${NAMESPACE}" logs "${pod_name}" --tail=50 || true
    warn "Helpful follow-ups:"
    warn "  kubectl ${kflag} -n ${NAMESPACE} describe pod ${pod_name}"
    warn "  kubectl ${kflag} -n ${NAMESPACE} get pods"
    warn "  kubectl ${kflag} -n ${NAMESPACE} logs -l app=nvidia-device-plugin-daemonset --tail=50"
  fi

  # Best-effort cleanup.
  # shellcheck disable=SC2086
  kubectl ${kflag} -n "${NAMESPACE}" delete pod "${pod_name}" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Print a summary of what was installed
# ---------------------------------------------------------------------------
print_summary() {
  echo ""
  echo -e "${GREEN}================================================================${NC}"
  echo -e "${GREEN} Phase 1 — GPU Operator Bootstrap Complete${NC}"
  echo -e "${GREEN}================================================================${NC}"
  echo "  Helm release   : ${RELEASE_NAME}"
  echo "  Namespace      : ${NAMESPACE}"
  echo "  Chart version  : ${GPU_OPERATOR_VERSION}"
  echo ""
  echo "  Components installed:"
  echo "    NVIDIA Driver       : ${DRIVER_ENABLED}"
  echo "    Container Toolkit   : ${TOOLKIT_ENABLED}"
  echo "    Device Plugin       : ${DEVICE_PLUGIN_ENABLED}"
  echo "    DCGM Exporter       : ${DCGM_ENABLED}"
  echo ""
  echo "  Useful follow-up commands:"
  local kflag; kflag=$(kubectl_flags)
  echo "    kubectl ${kflag} -n ${NAMESPACE} get pods          # operator pod status"
  echo "    kubectl ${kflag} get nodes -o wide                 # node overview"
  echo "    kubectl ${kflag} describe node <node-name>         # check GPU capacity/allocatable"
  echo ""
  echo "  Next step: Phase 2 — Storage, Ingress, and Observability"
  echo "  See: gpu-cluster-bootstrap-plan.md"
  echo -e "${GREEN}================================================================${NC}"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
  parse_args "$@"
  validate_args

  echo ""
  info "NVIDIA GPU Operator Bootstrap — Phase 1"
  [[ "${DRY_RUN}" == "true" ]] && warn "DRY-RUN mode: no changes will be made."
  [[ "${VALIDATION_ONLY}" == "true" ]] && warn "VALIDATION-ONLY mode: install steps will be skipped."
  echo ""

  preflight
  echo ""

  if [[ "${VALIDATION_ONLY}" == "false" ]]; then
    step_add_helm_repo
    echo ""

    step_install_gpu_operator
    echo ""
  fi

  if [[ "${SKIP_VALIDATION}" == "false" ]]; then
    step_wait_for_daemonsets
    echo ""

    step_validate_gpu_resource
    echo ""

    step_smoke_test
    echo ""
  else
    info "Skipping validation (--skip-validation passed)."
    echo ""
  fi

  print_summary
}

main "$@"
