#!/usr/bin/env python3
"""Reconcile model catalog ConfigMap into model runtime resources."""

# ADR: Kubernetes API access approach for model reconciliation
#
# Context:
# - This component reconciles catalog-driven desired state into Kubernetes
#   objects (ConfigMap, Service, StatefulSet) and deletes stale resources.
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

import yaml


MANAGED_BY_LABEL = "vllm-catalog-deployer"


@dataclass
class Config:
    namespace: str
    catalog_configmap: str
    catalog_key: str
    route_configmap: str
    route_key: str
    route_tenant_segment: str
    model_service_type: str
    vllm_image_default: str
    init_sync_image: str
    s3_secret_name: str
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
        description="Reconcile vLLM model-serving resources from a catalog ConfigMap",
    )
    parser.add_argument("-n", "--namespace", default="lm-serve")
    parser.add_argument("--catalog-configmap", default="lm-serve-model-catalog")
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
    parser.add_argument("--init-sync-image", default="amazon/aws-cli:2.22.14")
    parser.add_argument("--s3-secret", default="lm-serve-object-storage-creds")
    parser.add_argument("--storage-class", default="")
    parser.add_argument("--watch-interval-sec", default="0")
    parser.add_argument("--kubeconfig", default="")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    if not str(args.watch_interval_sec).isdigit():
        fatal("--watch-interval-sec must be a non-negative integer")

    return Config(
        namespace=args.namespace,
        catalog_configmap=args.catalog_configmap,
        catalog_key=args.catalog_key,
        route_configmap=args.route_configmap,
        route_key=args.route_key,
        route_tenant_segment=args.route_tenant_segment,
        model_service_type=args.model_service_type,
        vllm_image_default=args.vllm_image,
        init_sync_image=args.init_sync_image,
        s3_secret_name=args.s3_secret,
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


def fetch_catalog(config: Config) -> dict[str, Any]:
    escaped_key = config.catalog_key.replace(".", "\\.")
    cmd = kubectl_cmd(
        config,
        "-n",
        config.namespace,
        "get",
        "configmap",
        config.catalog_configmap,
        "-o",
        f"jsonpath={{.data.{escaped_key}}}",
    )
    payload = run_cmd(config, cmd, capture=True).strip()
    if not payload:
        fatal(
            f"Catalog ConfigMap {config.catalog_configmap} missing key {config.catalog_key} "
            f"in namespace {config.namespace}"
        )
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


def model_docs(config: Config, catalog: dict[str, Any], model: dict[str, Any]) -> list[dict[str, Any]]:
    model_name = str(model["name"])
    model_k8s = sanitize_name(model_name)

    serving = model.get("serving", {}) if isinstance(model.get("serving"), dict) else {}
    storage = model.get("storage", {}) if isinstance(model.get("storage"), dict) else {}
    catalog_storage = catalog.get("storage", {}) if isinstance(catalog.get("storage"), dict) else {}

    storage_prefix = storage.get("prefix")
    if not storage_prefix:
        fatal(f"Model {model_name} missing storage.prefix")

    bucket = catalog_storage.get("bucket")
    endpoint = catalog_storage.get("endpoint")
    region = catalog_storage.get("region", "us-east-1")
    if not bucket:
        fatal("Catalog storage.bucket must be set")
    if not endpoint:
        fatal("Catalog storage.endpoint must be set")

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
                                "name": config.s3_secret_name,
                                "key": "access_key_id",
                            }
                        },
                    },
                    {
                        "name": "AWS_SECRET_ACCESS_KEY",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": config.s3_secret_name,
                                "key": "secret_access_key",
                            }
                        },
                    },
                    {"name": "AWS_DEFAULT_REGION", "value": str(region)},
                    {"name": "S3_ENDPOINT", "value": str(endpoint)},
                    {"name": "BUCKET", "value": str(bucket)},
                    {"name": "MODEL_PREFIX", "value": str(storage_prefix)},
                ],
                "command": ["/bin/sh", "-c"],
                "args": [
                    "\n".join(
                        [
                            "set -euo pipefail",
                            "mkdir -p /models/current",
                            f"aws --endpoint-url \"{endpoint}\" s3 sync \"s3://{bucket}/{str(storage_prefix).rstrip('/')}/current/\" /models/current/ --no-progress",
                            f"aws --endpoint-url \"{endpoint}\" s3 cp \"s3://{bucket}/{str(storage_prefix).rstrip('/')}/manifest.json\" /models/manifest.json --no-progress || true",
                        ]
                    )
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
        catalog = fetch_catalog(config)

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
            docs = model_docs(config, catalog, model)
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
        "Reconciling vLLM catalog from ConfigMap "
        f"{config.catalog_configmap}/{config.catalog_key} in namespace {config.namespace}"
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
