#!/usr/bin/env python3
"""Reconcile object-storage catalog into model runtime resources."""

# ADR: Kubernetes API access approach for model reconciliation
#
# Context:
# - This component reconciles object-storage catalog desired state into
#   Kubernetes objects (ConfigMap, Service, StatefulSet) and deletes stale
#   resources.
# - The surrounding operational workflow in this repository is Helm + kubectl
#   driven, and operators already reason about deployments through those tools.
#
# Decision:
# - Use kubectl subprocess commands from Python (current implementation),
#   instead of a direct Kubernetes Python client integration.
#
# Why this is chosen now:
# - Operational parity: behavior maps directly to cluster commands operators
#   already run manually during troubleshooting.
# - Lower complexity: no in-process API client bootstrap, kubeconfig loading
#   branches, or per-resource patch/version handling logic is required.
# - Manifest-first flow: this reconciler already renders manifests and applies
#   them; kubectl apply naturally matches that pattern.
# - Faster delivery: keeps the reconciler implementation small while preserving
#   clear dry-run output and deterministic command logging.
#
# Alternatives considered:
# - Direct Kubernetes Python client (kubernetes package):
#   provides typed API calls, richer exceptions, and watch primitives, but
#   requires additional dependency/runtime surface and controller plumbing.
#
# Migration triggers (when this decision should be revisited):
# - Need event-driven watches/informers instead of interval polling.
# - Need fine-grained status updates/conditions as first-class API behavior.
# - Need ownerReferences/finalizers/leader-election style controller semantics.
# - Need higher reconcile throughput where subprocess overhead becomes material.
# - Need richer error classification than kubectl stderr/exit code mapping.
#
# Migration direction (future):
# - Introduce kubernetes Python client behind a small API adapter layer while
#   preserving existing reconcile inputs/outputs and labels.
# - Keep manifest rendering contract stable during transition to avoid Helm and
#   route-aggregation behavior drift.

from __future__ import annotations

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
import yaml


MANAGED_BY_LABEL = "vllm-catalog-deployer"
AUTH_ERROR_CODES = {
    "AccessDenied",
    "AccessDeniedException",
    "InvalidAccessKeyId",
    "InvalidToken",
    "SignatureDoesNotMatch",
    "TokenRefreshRequired",
    "UnrecognizedClientException",
    "ExpiredToken",
    "ExpiredTokenException",
}


@dataclass
class Config:
    namespace: str
    catalog_storage_bucket: str
    catalog_storage_endpoint: str
    catalog_storage_region: str
    catalog_storage_prefix: str
    catalog_tenant: str
    catalog_key: str
    route_configmap: str
    route_key: str
    route_tenant_segment: str
    model_service_type: str
    vllm_image_default: str
    init_sync_image: str
    s3_secret_name_a: str
    s3_secret_name_b: str
    s3_secret_placeholder_value: str
    require_ready_s3_secret: bool
    storage_class_name: str
    watch_interval_sec: int
    dry_run: bool
    kubeconfig_path: str


def info(msg: str) -> None:
    print(f"\033[0;36m[INFO]\033[0m  {msg}")


def ok(msg: str) -> None:
    print(f"\033[0;32m[OK]\033[0m    {msg}")


def warn(msg: str) -> None:
    print(f"\033[1;33m[WARN]\033[0m  {msg}")


def fatal(msg: str) -> None:
    print(f"\033[0;31m[ERROR]\033[0m {msg}", file=sys.stderr)
    raise SystemExit(1)


def parse_args() -> Config:
    parser = argparse.ArgumentParser(
        description="Reconcile vLLM model-serving resources from a tenant catalog in object storage",
    )
    parser.add_argument("-n", "--namespace", default="lm-serve")
    parser.add_argument("--catalog-storage-bucket", default="")
    parser.add_argument("--catalog-storage-endpoint", default="")
    parser.add_argument("--catalog-storage-region", default="")
    parser.add_argument("--catalog-storage-prefix", default="published-catalogs")
    parser.add_argument("--catalog-tenant", default="")
    parser.add_argument("--catalog-key", default="models.yaml")
    parser.add_argument("--route-configmap", default="lm-serve-envoy-routes")
    parser.add_argument("--route-key", default="routes.yaml")
    parser.add_argument(
        "--route-tenant-segment",
        default="",
        help="Optional route tenant segment for /m/<tenant>/<model>/ prefixes (default: namespace name)",
    )
    parser.add_argument(
        "--model-service-type",
        default="LoadBalancer",
        choices=["LoadBalancer", "NodePort", "ClusterIP"],
    )
    parser.add_argument("--vllm-image", default="vllm/vllm-openai:v0.8.5")
    parser.add_argument("--init-sync-image", default="")
    parser.add_argument("--s3-secret-a", default="lm-serve-object-storage-creds-a")
    parser.add_argument("--s3-secret-b", default="lm-serve-object-storage-creds-b")
    parser.add_argument(
        "--s3-secret-placeholder-value",
        default="REPLACE_ME_SET_REAL_CREDENTIAL",
        help="Placeholder value used in bootstrap pull secrets; reconciliation is skipped until at least one secret is updated",
    )
    parser.add_argument(
        "--require-ready-s3-secret",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Require at least one configured S3 secret to have non-placeholder credentials before reconciling",
    )
    parser.add_argument("--storage-class", default="")
    parser.add_argument("--watch-interval-sec", default="0")
    parser.add_argument("--kubeconfig", default="")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if not str(args.watch_interval_sec).isdigit():
        fatal("--watch-interval-sec must be a non-negative integer")
    if not args.catalog_storage_bucket.strip():
        fatal("--catalog-storage-bucket is required")
    if not args.catalog_storage_endpoint.strip():
        fatal("--catalog-storage-endpoint is required")
    if not args.catalog_storage_region.strip():
        fatal("--catalog-storage-region is required")
    if not args.catalog_tenant.strip():
        fatal("--catalog-tenant is required")
    if not args.init_sync_image.strip():
        fatal("--init-sync-image is required")

    return Config(
        namespace=args.namespace,
        catalog_storage_bucket=args.catalog_storage_bucket,
        catalog_storage_endpoint=args.catalog_storage_endpoint,
        catalog_storage_region=args.catalog_storage_region,
        catalog_storage_prefix=args.catalog_storage_prefix,
        catalog_tenant=args.catalog_tenant,
        catalog_key=args.catalog_key,
        route_configmap=args.route_configmap,
        route_key=args.route_key,
        route_tenant_segment=args.route_tenant_segment,
        model_service_type=args.model_service_type,
        vllm_image_default=args.vllm_image,
        init_sync_image=args.init_sync_image,
        s3_secret_name_a=args.s3_secret_a,
        s3_secret_name_b=args.s3_secret_b,
        s3_secret_placeholder_value=args.s3_secret_placeholder_value,
        require_ready_s3_secret=args.require_ready_s3_secret,
        storage_class_name=args.storage_class,
        watch_interval_sec=int(args.watch_interval_sec),
        dry_run=args.dry_run,
        kubeconfig_path=args.kubeconfig,
    )


def sanitize_name(raw: str) -> str:
    out = re.sub(r"[^a-z0-9-]+", "-", raw.lower())
    out = out.strip("-")
    return out


def bool_from_catalog(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    raw = str(value).strip().lower()
    return raw in {"true", "1", "yes", "on"}


def ensure_tools() -> None:
    if shutil.which("kubectl") is None:
        fatal("kubectl is required")


def kubectl_cmd(config: Config, *args: str) -> list[str]:
    cmd = ["kubectl"]
    if config.kubeconfig_path:
        cmd.append(f"--kubeconfig={config.kubeconfig_path}")
    cmd.extend(args)
    return cmd


def run_cmd(config: Config, cmd: list[str], capture: bool = True) -> str:
    result = subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=capture,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        fatal(f"Command failed ({result.returncode}): {' '.join(cmd)}\n{stderr}")
    return result.stdout if capture else ""


def run_apply(config: Config, manifest_path: Path) -> None:
    cmd = kubectl_cmd(config, "apply", "-f", str(manifest_path))
    if config.dry_run:
        print(f"\033[1;33m[DRY-RUN]\033[0m {' '.join(cmd)}")
        for line in manifest_path.read_text(encoding="utf-8").splitlines():
            print(f"    {line}")
        return
    run_cmd(config, cmd, capture=False)


def ensure_namespace(config: Config) -> None:
    get_ns = kubectl_cmd(config, "get", "namespace", config.namespace)
    result = subprocess.run(get_ns, check=False, text=True, capture_output=True)
    if result.returncode == 0:
        return
    create_ns = kubectl_cmd(config, "create", "namespace", config.namespace)
    if config.dry_run:
        print(f"\033[1;33m[DRY-RUN]\033[0m {' '.join(create_ns)}")
    else:
        run_cmd(config, create_ns, capture=False)


def load_catalog_payload(payload: str) -> dict[str, Any]:
    if not payload:
        fatal("Catalog payload is empty")

    try:
        catalog = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        fatal(f"Catalog YAML is invalid: {exc}")
    if not isinstance(catalog, dict):
        fatal("Catalog YAML must be a mapping")
    models = catalog.get("models")
    if not isinstance(models, list):
        fatal("Catalog is invalid: models must be a list")
    return catalog


def is_auth_error(exc: Exception) -> bool:
    if not isinstance(exc, ClientError):
        return False
    code = str(exc.response.get("Error", {}).get("Code", ""))
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in AUTH_ERROR_CODES or status == 401 or status == 403


def fetch_catalog_from_object_storage(config: Config, s3_secret_names: list[str]) -> tuple[dict[str, Any], str]:
    key = f"{config.catalog_storage_prefix.strip('/')}/{config.catalog_tenant.strip()}/{config.catalog_key.strip('/')}"
    auth_failures = 0

    for s3_secret_name in s3_secret_names:
        try:
            secret_data = fetch_secret_data(config, s3_secret_name)
        except SystemExit:
            warn(f"S3 pull secret {config.namespace}/{s3_secret_name} not found; trying next")
            continue
        access_key_id = secret_data.get("access_key_id", "").strip()
        secret_access_key = secret_data.get("secret_access_key", "").strip()
        if not access_key_id or not secret_access_key:
            warn(f"Secret {config.namespace}/{s3_secret_name} missing credentials; trying next")
            continue

        s3 = boto3.client(
            "s3",
            endpoint_url=config.catalog_storage_endpoint,
            region_name=config.catalog_storage_region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )

        try:
            response = s3.get_object(Bucket=config.catalog_storage_bucket, Key=key)
            body = response["Body"].read().decode("utf-8")
            return load_catalog_payload(body), s3_secret_name
        except Exception as exc:  # noqa: BLE001
            if is_auth_error(exc):
                auth_failures += 1
                warn(f"Catalog pull auth failed with secret {config.namespace}/{s3_secret_name}; trying next")
                continue
            fatal(
                "Failed to read published catalog from object storage: "
                f"s3://{config.catalog_storage_bucket}/{key} ({exc})"
            )

    if auth_failures > 0:
        fatal(f"All configured S3 secrets failed authentication while reading s3://{config.catalog_storage_bucket}/{key}")
    fatal(f"No usable S3 secret found for catalog pull in namespace {config.namespace}")


def fetch_secret_data(config: Config, secret_name: str) -> dict[str, str]:
    cmd = kubectl_cmd(
        config,
        "-n",
        config.namespace,
        "get",
        "secret",
        secret_name,
        "-o",
        "json",
    )
    output = run_cmd(config, cmd, capture=True)
    secret_doc = json.loads(output)
    raw_data = secret_doc.get("data", {})
    if not isinstance(raw_data, dict):
        return {}

    decoded: dict[str, str] = {}
    for key, encoded in raw_data.items():
        if not isinstance(encoded, str):
            continue
        try:
            decoded[key] = base64.b64decode(encoded).decode("utf-8")
        except Exception:  # noqa: BLE001
            decoded[key] = ""
    return decoded


def secret_is_ready(secret_data: dict[str, str], placeholder: str) -> bool:
    access_key_id = secret_data.get("access_key_id", "").strip()
    secret_access_key = secret_data.get("secret_access_key", "").strip()
    if not access_key_id or not secret_access_key:
        return False
    if access_key_id == placeholder or secret_access_key == placeholder:
        return False
    return True


def resolve_s3_secret_candidates(config: Config) -> list[str]:
    names = [config.s3_secret_name_a.strip(), config.s3_secret_name_b.strip()]
    candidates: list[str] = []
    for name in names:
        if name and name not in candidates:
            candidates.append(name)

    if not candidates:
        fatal("At least one of --s3-secret-a or --s3-secret-b must be non-empty")

    info(f"S3 pull secret candidates: {', '.join(candidates)}")

    if not config.require_ready_s3_secret:
        return candidates

    ready: list[str] = []
    for secret_name in candidates:
        try:
            secret_data = fetch_secret_data(config, secret_name)
        except SystemExit:
            warn(f"S3 pull secret {config.namespace}/{secret_name} not found; trying next")
            continue
        if secret_is_ready(secret_data, config.s3_secret_placeholder_value):
            ready.append(secret_name)
        else:
            warn(f"S3 pull secret {config.namespace}/{secret_name} still has placeholder or empty credentials; trying next")

    return ready


def enabled_models(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    models = catalog.get("models", [])
    out: list[dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        if not bool_from_catalog(model.get("enabled", True)):
            continue
        if not model.get("name"):
            continue
        out.append(model)
    return out


def write_manifest(tmpdir: Path, name: str, docs: list[dict[str, Any]]) -> Path:
    manifest_path = tmpdir / name
    manifest_path.write_text(yaml.safe_dump_all(docs, sort_keys=False), encoding="utf-8")
    return manifest_path


def render_route_configmap(config: Config, catalog: dict[str, Any], route_tenant_segment: str) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    for model in enabled_models(catalog):
        model_name = str(model["name"])
        model_k8s = sanitize_name(model_name)
        serving = model.get("serving", {}) if isinstance(model.get("serving"), dict) else {}
        service_port = int(serving.get("port", 8000))
        routes.append(
            {
                "prefix": f"/m/{route_tenant_segment}/{model_name}/",
                "cluster": f"vllm-{model_k8s}",
                "serviceHost": f"vllm-{model_k8s}.{config.namespace}.svc.cluster.local",
                "servicePort": service_port,
            }
        )

    route_yaml = yaml.safe_dump({"routes": routes}, sort_keys=False)
    route_json = json.dumps(routes)

    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": config.route_configmap,
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": MANAGED_BY_LABEL,
                "app.kubernetes.io/component": "model-runtime",
                "lm-serve/model-route-source": "true",
                "lm-serve.ai/route-source-owner": "lm-serve-models",
            },
        },
        "data": {
            config.route_key: route_yaml,
            "routes.json": route_json,
        },
    }


def model_docs(config: Config, model: dict[str, Any], s3_secret_name: str) -> list[dict[str, Any]]:
    model_name = str(model["name"])
    model_k8s = sanitize_name(model_name)

    serving = model.get("serving", {}) if isinstance(model.get("serving"), dict) else {}
    storage = model.get("storage", {}) if isinstance(model.get("storage"), dict) else {}

    storage_prefix = storage.get("prefix")
    if not storage_prefix:
        fatal(f"Model {model_name} missing storage.prefix")

    bucket = config.catalog_storage_bucket
    endpoint = config.catalog_storage_endpoint
    region = config.catalog_storage_region

    replicas = int(serving.get("replicas", 1))
    gpu_count = int(serving.get("gpuCount", 1))
    pvc_size = str(serving.get("pvcSize", "100Gi"))
    port = int(serving.get("port", 8000))
    tensor_parallel = int(serving.get("tensorParallelSize", 1))
    max_model_len = int(serving.get("maxModelLen", 8192))
    dtype = str(serving.get("dtype", "auto"))
    image = str(serving.get("image", config.vllm_image_default))

    extra_args = serving.get("extraArgs", [])
    if not isinstance(extra_args, list):
        extra_args = []

    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": f"vllm-{model_k8s}",
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": MANAGED_BY_LABEL,
                "app.kubernetes.io/component": "model-runtime",
                "app.kubernetes.io/name": f"vllm-{model_k8s}",
                "lm-serve/model-name": model_name,
            },
        },
        "spec": {
            "selector": {"app.kubernetes.io/name": f"vllm-{model_k8s}"},
            "ports": [{"name": "http", "port": 8000, "targetPort": port}],
        },
    }

    pod_spec: dict[str, Any] = {
        "restartPolicy": "Always",
        "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
        "initContainers": [
            {
                "name": "model-sync",
                "image": config.init_sync_image,
                "imagePullPolicy": "IfNotPresent",
                "env": [
                    {
                        "name": "AWS_ACCESS_KEY_ID",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": s3_secret_name,
                                "key": "access_key_id",
                            }
                        },
                    },
                    {
                        "name": "AWS_SECRET_ACCESS_KEY",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": s3_secret_name,
                                "key": "secret_access_key",
                            }
                        },
                    },
                    {"name": "AWS_DEFAULT_REGION", "value": str(region)},
                ],
                "command": ["python3", "/app/sync_model_from_manifest.py"],
                "args": [
                    "--bucket",
                    str(bucket),
                    "--endpoint",
                    str(endpoint),
                    "--region",
                    str(region),
                    "--model-prefix",
                    str(storage_prefix),
                    "--output-dir",
                    "/models/current",
                    "--manifest-output",
                    "/models/manifest.json",
                ],
                "volumeMounts": [{"name": "model-cache", "mountPath": "/models"}],
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "runAsGroup": 1000,
                },
            }
        ],
        "containers": [
            {
                "name": "vllm",
                "image": image,
                "imagePullPolicy": "IfNotPresent",
                "args": [
                    "--host",
                    "0.0.0.0",
                    "--port",
                    str(port),
                    "--model",
                    "/models/current",
                    "--served-model-name",
                    model_name,
                    "--tensor-parallel-size",
                    str(tensor_parallel),
                    "--max-model-len",
                    str(max_model_len),
                    "--dtype",
                    dtype,
                    *[str(x) for x in extra_args if str(x).strip()],
                ],
                "ports": [{"containerPort": port, "name": "http"}],
                "readinessProbe": {
                    "httpGet": {"path": "/health", "port": port},
                    "initialDelaySeconds": 20,
                    "periodSeconds": 15,
                    "timeoutSeconds": 5,
                },
                "livenessProbe": {
                    "httpGet": {"path": "/health", "port": port},
                    "initialDelaySeconds": 60,
                    "periodSeconds": 30,
                    "timeoutSeconds": 5,
                },
                "resources": {
                    "limits": {"nvidia.com/gpu": str(gpu_count)},
                    "requests": {"nvidia.com/gpu": str(gpu_count)},
                },
                "volumeMounts": [{"name": "model-cache", "mountPath": "/models"}],
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "runAsGroup": 1000,
                },
            }
        ],
    }

    for k, spec_key in (
        ("runtimeClassName", "runtimeClassName"),
        ("priorityClassName", "priorityClassName"),
        ("serviceAccountName", "serviceAccountName"),
    ):
        value = serving.get(k)
        if value:
            pod_spec[spec_key] = value

    for k in ("nodeSelector", "tolerations", "affinity"):
        value = serving.get(k)
        if isinstance(value, (dict, list)) and len(value) > 0:
            pod_spec[k] = value

    pvc_spec: dict[str, Any] = {
        "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": pvc_size}},
    }
    if config.storage_class_name:
        pvc_spec["storageClassName"] = config.storage_class_name

    statefulset = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {
            "name": f"vllm-{model_k8s}",
            "namespace": config.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": MANAGED_BY_LABEL,
                "app.kubernetes.io/component": "model-runtime",
                "app.kubernetes.io/name": f"vllm-{model_k8s}",
                "lm-serve/model-name": model_name,
            },
        },
        "spec": {
            "replicas": replicas,
            "serviceName": f"vllm-{model_k8s}",
            "podManagementPolicy": "Parallel",
            "selector": {"matchLabels": {"app.kubernetes.io/name": f"vllm-{model_k8s}"}},
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/managed-by": MANAGED_BY_LABEL,
                        "app.kubernetes.io/component": "model-runtime",
                        "app.kubernetes.io/name": f"vllm-{model_k8s}",
                        "lm-serve/model-name": model_name,
                    },
                    "annotations": {
                        "prometheus.io/scrape": "true",
                        "prometheus.io/port": str(port),
                        "prometheus.io/path": "/metrics",
                    },
                },
                "spec": pod_spec,
            },
            "volumeClaimTemplates": [
                {
                    "metadata": {
                        "name": "model-cache",
                        "labels": {
                            "app.kubernetes.io/managed-by": MANAGED_BY_LABEL,
                            "app.kubernetes.io/component": "model-runtime",
                        },
                    },
                    "spec": pvc_spec,
                }
            ],
        },
    }

    return [service, statefulset]


def cleanup_removed_models(config: Config, desired_names: set[str]) -> None:
    cmd = kubectl_cmd(
        config,
        "-n",
        config.namespace,
        "get",
        "statefulsets",
        "-l",
        f"app.kubernetes.io/managed-by={MANAGED_BY_LABEL},app.kubernetes.io/component=model-runtime",
        "-o",
        "json",
    )
    output = run_cmd(config, cmd, capture=True)
    existing = json.loads(output)

    for item in existing.get("items", []):
        name = item.get("metadata", {}).get("name")
        if not name or name in desired_names:
            continue
        warn(f"Removing stale model runtime: {name}")
        for resource in ("statefulset", "service"):
            del_cmd = kubectl_cmd(
                config,
                "-n",
                config.namespace,
                "delete",
                resource,
                name,
                "--ignore-not-found",
            )
            if config.dry_run:
                print(f"\033[1;33m[DRY-RUN]\033[0m {' '.join(del_cmd)}")
            else:
                run_cmd(config, del_cmd, capture=False)


def reconcile_once(config: Config) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmpdir = Path(td)
        s3_secret_candidates = resolve_s3_secret_candidates(config)
        if not s3_secret_candidates:
            ok(f"Reconciliation skipped for namespace {config.namespace} until at least one pull secret is ready")
            return

        catalog, s3_secret_name = fetch_catalog_from_object_storage(config, s3_secret_candidates)

        route_tenant_segment = sanitize_name(config.route_tenant_segment or config.namespace)
        if not route_tenant_segment:
            fatal("Route tenant segment resolved to empty string")

        desired_names: set[str] = set()
        models = enabled_models(catalog)

        for model in models:
            model_name = str(model["name"])
            model_k8s = sanitize_name(model_name)
            if not model_k8s:
                fatal(f"Model name produces empty kubernetes suffix: {model_name}")
            docs = model_docs(config, model, s3_secret_name)
            model_manifest = write_manifest(tmpdir, f"model-{model_k8s}.yaml", docs)
            run_apply(config, model_manifest)
            desired_names.add(f"vllm-{model_k8s}")

        if not desired_names:
            warn("No enabled models found in catalog; applying empty route set and cleaning stale runtimes")

        route_cm = render_route_configmap(config, catalog, route_tenant_segment)
        route_manifest = write_manifest(tmpdir, "route-configmap.yaml", [route_cm])
        run_apply(config, route_manifest)

        cleanup_removed_models(config, desired_names)

        ok(f"Reconciliation completed for namespace {config.namespace}")


def main() -> int:
    config = parse_args()
    ensure_tools()
    ensure_namespace(config)

    info(
        "Reconciling vLLM catalog from object storage "
        f"s3://{config.catalog_storage_bucket}/{config.catalog_storage_prefix.strip('/')}/{config.catalog_tenant}/{config.catalog_key} "
        f"for namespace {config.namespace}"
    )

    if config.watch_interval_sec == 0:
        reconcile_once(config)
        return 0

    info(f"Watch mode enabled: interval {config.watch_interval_sec}s")
    while True:
        try:
            reconcile_once(config)
        except Exception as exc:  # noqa: BLE001
            warn(f"Reconcile attempt failed; retrying at next interval: {exc}")
        time.sleep(config.watch_interval_sec)


if __name__ == "__main__":
    raise SystemExit(main())
