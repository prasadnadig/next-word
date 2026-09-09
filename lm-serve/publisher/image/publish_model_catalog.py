#!/usr/bin/env python3
"""Publishes tenant-scoped model artifacts to S3-compatible storage.

Catalog inputs are split into two layers:

1) Global catalog (one file/ConfigMap, shared across all tenants)
     Defines every known model once: unique `name`, `source`, `storage.prefix`,
     and a base `serving` block. This is the single source of truth for a
     model's identity and default serving configuration.

2) Tenant selection (one file/ConfigMap per tenant)
     Lists which global model `name`s are enabled for that tenant, plus an
     optional `enabled` flag and `servingOverrides` (deep-merged onto the
     global `serving` block) for that tenant only.

This script reads the global catalog plus each tenant's selection, resolves
them into fully-specified per-tenant model lists, downloads model artifacts,
uploads them to object storage, and writes published tenant catalog files
consumed by the Python model deployment reconciler. The resolution step is an
input-side concern only; the published tenant catalog object format written to
object storage is unchanged.

Publication uses shared-artifact deduplication plus manifest-pointer updates
so serving paths only observe fully published model revisions.

Input mode design (why two modes exist):

1) File mode (`--global-catalog-file PATH` + `--catalog-tenant-file TENANT=PATH`, repeatable)
     Use when running locally, in CI, or across clusters where catalog YAML
     files are available on disk.
     Example:
         python3 publish_model_catalog.py \
             --storage-bucket my-lm-serve-models \
             --storage-endpoint https://us-ord-1.linodeobjects.com \
             --storage-region us-east-1 \
             --global-catalog-file ./catalogs/models.yaml \
             --catalog-tenant-file dev-west=./catalogs/dev-west.yaml \
             --catalog-tenant-file staging=./catalogs/staging.yaml

2) ConfigMap mode (`--global-catalog-configmap NAME` + `--catalog-configmap NAME` + `--catalog-tenant TENANT`, repeatable pairs)
     Use for in-cluster publisher runs where source catalogs are already materialized
     as ConfigMaps and should be fetched from Kubernetes API.
     Example:
         python3 publish_model_catalog.py \
             --storage-bucket my-lm-serve-models \
             --storage-endpoint https://us-ord-1.linodeobjects.com \
             --storage-region us-east-1 \
             --catalog-namespace lm-serve-publisher \
             --global-catalog-configmap lm-serve-model-catalog-global \
             --catalog-configmap lm-serve-model-catalog-dev-west \
             --catalog-tenant dev-west \
             --catalog-configmap lm-serve-model-catalog-staging \
             --catalog-tenant staging

The modes are mutually exclusive by design to keep operator intent explicit.

Storage access credentials are read-in from environment variables; two active sets to help with rotation/failover:
    S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY
    S3_ACCESS_KEY_ID_B, S3_SECRET_ACCESS_KEY_B.

Hugging Face model downloads require a valid token in the environment variable specified by each model's `source.authEnv` field (default: HF_TOKEN).

Supported source types:
- huggingface

Model revisions are expected to be immutable (a pinned tag or commit SHA, not a moving
branch like "main"). Once a model's source identity (type:repo@revision) has been
published, its manifest pointer is treated as authoritative and the model is not
re-downloaded/re-uploaded on subsequent runs unless explicitly requested via
--force-redownload TYPE:REPO@REVISION (repeatable). Catalog `name` is now globally
unique (defined once in the global catalog), but force-redownload still matches on
source identity so it keeps working even if a model is renamed.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import shutil
import tempfile
import threading
import uuid
import re
from urllib.parse import urlparse
from typing import Dict, Iterable, List, Optional, Tuple

import boto3
import yaml

from botocore.client import BaseClient
from botocore.exceptions import ClientError
from huggingface_hub import snapshot_download
from kubernetes import client, config


@dataclasses.dataclass(frozen=True)
class StorageConfig:
    bucket: str
    endpoint: str
    region: str


@dataclasses.dataclass(frozen=True)
class SourceConfig:
    source_type: str
    repo: str
    revision: str
    auth_env: str


@dataclasses.dataclass(frozen=True)
class ModelConfig:
    name: str
    enabled: bool
    source: SourceConfig
    storage_prefix: str
    serving: Dict[str, object] = dataclasses.field(default_factory=dict)

    @property
    def pvc_size(self) -> Optional[str]:
        # derived from serving rather than stored, since it's just serving["pvcSize"]
        return str(self.serving.get("pvcSize", "")).strip() or None


@dataclasses.dataclass(frozen=True)
class Catalog:
    models: List[ModelConfig]


@dataclasses.dataclass(frozen=True)
class TenantCatalog:
    tenant: str
    catalog: Catalog


@dataclasses.dataclass(frozen=True)
class TenantSelectionEntry:
    name: str
    enabled: bool
    serving_overrides: Dict[str, object]


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


def is_auth_error(exc: Exception) -> bool:
    if not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in AUTH_ERROR_CODES or status == 401 or status == 403


class S3ClientPool:
    def __init__(self, clients: List[BaseClient]):
        if not clients:
            raise ValueError("At least one S3 client is required")
        self._clients = clients

        # Preferred index is the index of the last successful client used for an operation.
        self._preferred_index = 0
        self._preferred_index_lock = threading.Lock()

    def call(self, operation: str, fn):

        # Determine the order in which to try the S3 clients, starting with the preferred one. Preferred index is determined by the last successful client used for an operation. If the preferred client fails due to authentication, we try the next one in the list.
        with self._preferred_index_lock:
            ordered_indexes = [self._preferred_index] + [
                i for i in range(len(self._clients)) if i != self._preferred_index
            ]

        last_auth_exc: Optional[Exception] = None
        for index in ordered_indexes:
            s3 = self._clients[index]
            try:
                result = fn(s3)
                with self._preferred_index_lock:
                    self._preferred_index = index
                return result
            except Exception as exc:  # noqa: BLE001
                if is_auth_error(exc):
                    last_auth_exc = exc
                    if len(self._clients) > 1:
                        print(
                            f"[WARN] S3 auth failed during {operation} on credential slot {index + 1}; trying next credential"
                        )
                    continue
                raise

        if last_auth_exc is not None:
            raise RuntimeError(f"All configured S3 credentials failed authentication during {operation}") from last_auth_exc
        raise RuntimeError(f"S3 operation failed without authentication fallback path: {operation}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish model artifacts to S3-compatible storage")
    parser.add_argument("--global-catalog-file", default="", help="Path to the global model catalog YAML (file mode); shared across all tenant selections")
    parser.add_argument("--global-catalog-configmap", default="", help="ConfigMap name holding the global model catalog (ConfigMap mode)")
    parser.add_argument("--global-catalog-key", default="models.yaml", help="ConfigMap data key for the global model catalog")
    parser.add_argument("--catalog-tenant-file", action="append", default=[], help="Repeatable TENANT=PATH (or TENANT:PATH) mapping for tenant selection files (example: --catalog-tenant-file dev=./dev.yaml)")
    parser.add_argument("--catalog-configmap", action="append", default=[], help="Repeatable source ConfigMap names for ConfigMap mode; each entry must pair with one --catalog-tenant")
    parser.add_argument("--catalog-tenant", action="append", default=[], help="Repeatable tenant labels paired positionally with --catalog-configmap entries")
    parser.add_argument("--catalog-namespace", default="lm-serve-publisher", help="ConfigMap namespace")
    parser.add_argument("--catalog-key", default="models.yaml", help="ConfigMap data key for tenant selection ConfigMaps")
    parser.add_argument("--storage-bucket", default="", help="Object storage bucket for published catalogs and model artifacts")
    parser.add_argument("--storage-endpoint", default="", help="Object storage endpoint URL (http/https)")
    parser.add_argument("--storage-region", default="", help="Object storage region")
    parser.add_argument("--run-id", default="", help="Optional run ID for staging prefix")
    parser.add_argument("--model", default="", help="Optional single model name filter")
    parser.add_argument("--max-models", type=int, default=0, help="Optional cap on number of models to publish")
    parser.add_argument("--hf-max-workers", type=int, default=8, help="Maximum concurrent workers used by Hugging Face snapshot downloads for each model")
    parser.add_argument("--upload-max-workers", type=int, default=4, help="Maximum concurrent workers used to upload shared model artifacts to object storage; set to 1 for sequential uploads")
    parser.add_argument("--shared-artifacts-prefix", default="_shared", help="S3 prefix used for deduplicated artifacts shared across tenant catalogs")
    parser.add_argument("--published-catalogs-prefix", default="published-catalogs", help="Object storage prefix for published per-tenant catalog files")
    parser.add_argument("--published-catalog-key", default="models.yaml", help="Filename written for each published tenant catalog under published-catalogs-prefix")
    parser.add_argument("--force-redownload", action="append", default=[], help="Repeatable source identity TYPE:REPO@REVISION (e.g. huggingface:org/repo@v1) to force redownload/republish even if already published")
    args = parser.parse_args()

    has_file_mode = bool(args.catalog_tenant_file)
    has_configmap_mode = bool(args.catalog_configmap)

    if has_file_mode and has_configmap_mode:
        parser.error("--catalog-tenant-file and --catalog-configmap/--catalog-tenant modes are mutually exclusive")
    if not has_file_mode and not has_configmap_mode:
        parser.error("one input mode is required: --catalog-tenant-file or --catalog-configmap with --catalog-tenant")

    if has_file_mode and args.catalog_tenant:
        parser.error("--catalog-tenant is only valid with --catalog-configmap mode")

    if has_configmap_mode:
        if not args.catalog_tenant:
            parser.error("--catalog-configmap mode requires --catalog-tenant entries")
        if len(args.catalog_tenant) != len(args.catalog_configmap):
            parser.error("the number of --catalog-tenant values must match --catalog-configmap values")

    if has_file_mode and not str(args.global_catalog_file).strip():
        parser.error("--global-catalog-file is required in file mode")
    if has_configmap_mode and not str(args.global_catalog_configmap).strip():
        parser.error("--global-catalog-configmap is required in ConfigMap mode")

    if args.hf_max_workers < 1:
        parser.error("--hf-max-workers must be at least 1")
    if args.upload_max_workers < 1:
        parser.error("--upload-max-workers must be at least 1")

    if not str(args.storage_bucket).strip():
        parser.error("--storage-bucket is required")
    if not str(args.storage_endpoint).strip():
        parser.error("--storage-endpoint is required")
    if not str(args.storage_region).strip():
        parser.error("--storage-region is required")

    parsed_endpoint = urlparse(str(args.storage_endpoint).strip())
    if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
        parser.error(
            "--storage-endpoint must be a valid absolute URL with http/https scheme "
            f"(received: {args.storage_endpoint!r})"
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", str(args.storage_region).strip()):
        parser.error(
            "--storage-region must be 1-63 characters, alphanumeric or hyphen, and start with an alphanumeric "
            f"(received: {args.storage_region!r})"
        )

    return args


def load_catalog_text_from_configmap(namespace: str, name: str, key: str) -> str:
    """Load model catalog YAML from Kubernetes ConfigMap data key."""
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    api = client.CoreV1Api()
    cm = api.read_namespaced_config_map(name=name, namespace=namespace)
    if not cm.data or key not in cm.data:
        raise ValueError(f"ConfigMap {namespace}/{name} missing data key {key}")
    return cm.data[key]


def parse_global_catalog(text: str) -> Catalog:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("Global catalog must be a YAML mapping")

    models_raw = raw.get("models", [])
    if not isinstance(models_raw, list):
        raise ValueError("Global catalog models must be a list")

    models: Dict[str, ModelConfig] = {}
    prefixes_seen: Dict[str, str] = {}
    for item in models_raw:
        if not isinstance(item, dict):
            continue

        name = str(item["name"])
        if name in models:
            raise ValueError(f"Global catalog defines duplicate model name '{name}'")

        storage_prefix = str(item.get("storage", {}).get("prefix", "")).strip("/")
        if not storage_prefix:
            raise ValueError(f"Model {name} missing storage.prefix")
        if storage_prefix in prefixes_seen:
            raise ValueError(
                f"Global catalog models '{prefixes_seen[storage_prefix]}' and '{name}' "
                f"share storage.prefix '{storage_prefix}'; each model needs a unique prefix"
            )
        prefixes_seen[storage_prefix] = name

        serving_raw = item.get("serving", {})
        if not isinstance(serving_raw, dict):
            raise ValueError(f"Model {name} serving block must be a mapping")

        source_raw = item.get("source", {})
        models[name] = ModelConfig(
            name=name,
            enabled=bool(item.get("enabled", True)),
            source=SourceConfig(
                source_type=str(source_raw.get("type", "huggingface")),
                repo=str(source_raw["repo"]),
                revision=str(source_raw.get("revision", "main")),
                auth_env=str(source_raw.get("authEnv", "HF_TOKEN")),
            ),
            storage_prefix=storage_prefix,
            serving=serving_raw,
        )

    return Catalog(models=list(models.values()))


def parse_tenant_selection(text: str) -> List[TenantSelectionEntry]:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("Tenant selection must be a YAML mapping")

    models_raw = raw.get("models", [])
    if not isinstance(models_raw, list):
        raise ValueError("Tenant selection models must be a list")

    entries: List[TenantSelectionEntry] = []
    seen_names: set = set()
    for item in models_raw:
        if not isinstance(item, dict):
            continue

        name = str(item["name"])
        if name in seen_names:
            raise ValueError(f"Tenant selection references model '{name}' more than once")
        seen_names.add(name)

        serving_overrides = item.get("servingOverrides", {})
        if not isinstance(serving_overrides, dict):
            raise ValueError(f"Tenant selection entry '{name}' servingOverrides must be a mapping")

        entries.append(
            TenantSelectionEntry(
                name=name,
                enabled=bool(item.get("enabled", True)),
                serving_overrides=serving_overrides,
            )
        )

    return entries


def deep_merge_dicts(base: Dict[str, object], override: Dict[str, object]) -> Dict[str, object]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge_dicts(result[key], value)  # type: ignore[arg-type]
        else:
            result[key] = value
    return result


def resolve_tenant_catalog(global_catalog: Catalog, selection: List[TenantSelectionEntry], tenant: str) -> Catalog:
    by_name = {m.name: m for m in global_catalog.models}
    models: List[ModelConfig] = []
    for entry in selection:
        global_model = by_name.get(entry.name)
        if global_model is None:
            raise ValueError(f"Tenant '{tenant}' references unknown model '{entry.name}' not present in the global catalog")

        serving = deep_merge_dicts(global_model.serving, entry.serving_overrides)
        models.append(
            ModelConfig(
                name=global_model.name,
                # a model must be enabled at both the global and tenant level to publish
                enabled=global_model.enabled and entry.enabled,
                source=global_model.source,
                storage_prefix=global_model.storage_prefix,
                serving=serving,
            )
        )
    return Catalog(models=models)


def parse_tenant_catalog_spec(spec: str) -> Tuple[str, str]:
    if "=" in spec:
        tenant, path = spec.split("=", 1)
    elif ":" in spec:
        tenant, path = spec.split(":", 1)
    else:
        raise ValueError(f"Invalid catalog tenant spec '{spec}'. Expected TENANT=PATH or TENANT:PATH")
    tenant = tenant.strip()
    path = path.strip()
    if not tenant:
        raise ValueError(f"Invalid catalog tenant spec '{spec}'. Tenant name cannot be empty")
    if not path:
        raise ValueError(f"Invalid catalog tenant spec '{spec}'. Catalog path cannot be empty")
    return tenant, path


def model_config_to_dict(model: ModelConfig) -> Dict[str, object]:
    return {
        "name": model.name,
        "enabled": model.enabled,
        "source": {
            "type": model.source.source_type,
            "repo": model.source.repo,
            "revision": model.source.revision,
            "authEnv": model.source.auth_env,
        },
        "storage": {"prefix": model.storage_prefix},
    }


def model_identity(model: ModelConfig) -> str:
    # identity independent of catalog `name`, kept for force-redownload matching robustness across renames
    return f"{model.source.source_type}:{model.source.repo}@{model.source.revision}"


def catalog_to_tenant_yaml(catalog: Catalog, tenant: str) -> str:
    payload = {
        "models": [model_config_to_dict(model) for model in catalog.models],
    }
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def parse_storage_config(args: argparse.Namespace) -> StorageConfig:
    bucket = str(args.storage_bucket).strip()
    endpoint = str(args.storage_endpoint).strip()
    region = str(args.storage_region).strip()

    return StorageConfig(bucket=bucket, endpoint=endpoint, region=region)


def read_local_catalog_text(path: str) -> str:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    if isinstance(parsed, dict) and parsed.get("kind") == "ConfigMap":
        data = parsed.get("data", {})
        if not isinstance(data, dict):
            raise ValueError(f"ConfigMap manifest in {path} does not contain a data map")
        if "models.yaml" not in data:
            raise ValueError(f"ConfigMap manifest in {path} missing data key 'models.yaml'")
        text = str(data["models.yaml"])
    return text


def load_global_catalog(args: argparse.Namespace) -> Catalog:
    if args.catalog_tenant_file:
        text = read_local_catalog_text(args.global_catalog_file)
    else:
        text = load_catalog_text_from_configmap(
            namespace=args.catalog_namespace,
            name=args.global_catalog_configmap,
            key=args.global_catalog_key,
        )
    return parse_global_catalog(text)


def load_catalogs(args: argparse.Namespace) -> List[TenantCatalog]:
    global_catalog = load_global_catalog(args)

    if args.catalog_tenant_file:
        entries: List[TenantCatalog] = []
        for spec in args.catalog_tenant_file:
            tenant, path = parse_tenant_catalog_spec(spec)
            selection = parse_tenant_selection(read_local_catalog_text(path))
            catalog = resolve_tenant_catalog(global_catalog, selection, tenant)
            entries.append(
                TenantCatalog(
                    tenant=tenant,
                    catalog=catalog,
                )
            )
        return entries

    if args.catalog_configmap:
        names = args.catalog_configmap
        tenant_names = args.catalog_tenant
        entries: List[TenantCatalog] = []
        for name, tenant_name in zip(names, tenant_names):
            text = load_catalog_text_from_configmap(
                namespace=args.catalog_namespace,
                name=name,
                key=args.catalog_key,
            )
            selection = parse_tenant_selection(text)
            tenant = tenant_name.strip()
            catalog = resolve_tenant_catalog(global_catalog, selection, tenant)
            entries.append(
                TenantCatalog(
                    tenant=tenant,
                    catalog=catalog,
                )
            )
        return entries

    raise ValueError("No catalog inputs were provided")


def s3_client_pool(storage: StorageConfig) -> S3ClientPool:
    credential_candidates: List[Tuple[str, str]] = []

    primary_access_key = os.environ.get("S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
    primary_secret_key = os.environ.get("S3_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")
    if primary_access_key and primary_secret_key:
        credential_candidates.append((primary_access_key, primary_secret_key))

    secondary_access_key = (
        os.environ.get("S3_ACCESS_KEY_ID_B")
        or os.environ.get("AWS_ACCESS_KEY_ID_B")
        or os.environ.get("S3_ACCESS_KEY_ID_2")
        or os.environ.get("AWS_ACCESS_KEY_ID_2")
    )
    secondary_secret_key = (
        os.environ.get("S3_SECRET_ACCESS_KEY_B")
        or os.environ.get("AWS_SECRET_ACCESS_KEY_B")
        or os.environ.get("S3_SECRET_ACCESS_KEY_2")
        or os.environ.get("AWS_SECRET_ACCESS_KEY_2")
    )
    if secondary_access_key and secondary_secret_key:
        secondary_pair = (secondary_access_key, secondary_secret_key)
        if secondary_pair not in credential_candidates:
            credential_candidates.append(secondary_pair)

    if not credential_candidates:
        raise ValueError(
            "Missing S3 credentials in env. Provide S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY and optionally "
            "S3_ACCESS_KEY_ID_B/S3_SECRET_ACCESS_KEY_B"
        )

    clients = [
        boto3.client(
            "s3",
            endpoint_url=storage.endpoint,
            region_name=storage.region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        for access_key, secret_key in credential_candidates
    ]
    return S3ClientPool(clients)


def publish_tenant_catalog(
    s3_pool: S3ClientPool,
    storage: StorageConfig,
    tenant_catalog: TenantCatalog,
    output_prefix: str,
    output_key: str,
) -> str:
    prefix = output_prefix.strip("/")
    key_name = output_key.strip("/")
    if not prefix:
        raise ValueError("--published-catalogs-prefix cannot be empty")
    if not key_name:
        raise ValueError("--published-catalog-key cannot be empty")

    catalog_key = f"{prefix}/{tenant_catalog.tenant}/{key_name}"
    body = catalog_to_tenant_yaml(tenant_catalog.catalog, tenant_catalog.tenant).encode("utf-8")
    s3_pool.call(
        "put_object(published-catalog)",
        lambda s3: s3.put_object(Bucket=storage.bucket, Key=catalog_key, Body=body, ContentType="application/x-yaml"),
    )
    return catalog_key


def file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(root: pathlib.Path) -> List[pathlib.Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def parse_pvc_size_to_bytes(value: Optional[str]) -> int:
    if value is None:
        raise ValueError("Model has no serving.pvcSize configured")

    text = str(value).strip()
    if not text:
        raise ValueError("Model serving.pvcSize is empty")

    match = re.fullmatch(r"(?i)\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]i?|[kmgt]?|b)?\s*", text)
    if not match:
        raise ValueError(f"Unsupported pvcSize value {value!r}; expected values like 120Gi, 150G, or 5368709120")

    size = float(match.group(1))
    suffix = (match.group(2) or "b").lower()
    multipliers = {
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "ki": 1024,
        "m": 1024 ** 2,
        "mb": 1024 ** 2,
        "mi": 1024 ** 2,
        "g": 1024 ** 3,
        "gb": 1024 ** 3,
        "gi": 1024 ** 3,
        "t": 1024 ** 4,
        "tb": 1024 ** 4,
        "ti": 1024 ** 4,
    }
    if suffix not in multipliers:
        raise ValueError(f"Unsupported pvcSize unit {suffix!r} in {value!r}")
    return int(math.ceil(size * multipliers[suffix]))


def format_bytes(n: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    value = float(n)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024.0
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.2f} {units[index]}"


def model_download_size_bytes(model: ModelConfig) -> int:
    if not model.pvc_size:
        raise ValueError(f"Model {model.name} is missing serving.pvcSize; cannot estimate local download footprint")
    return int(math.ceil(parse_pvc_size_to_bytes(model.pvc_size) * 1.2))


def estimate_download_space_for_catalogs(tenant_catalogs: List[TenantCatalog], args: argparse.Namespace) -> Tuple[int, int, List[Tuple[str, str, int, int]]]:
    entries: List[Tuple[str, str, int, int]] = []
    total_required = 0
    peak_required = 0

    for tenant_catalog in tenant_catalogs:
        selected = filtered_models(args, tenant_catalog.catalog.models)
        for model in selected:
            required = model_download_size_bytes(model)
            total_required += required
            peak_required = max(peak_required, required)
            entries.append((tenant_catalog.tenant, model.name, required, max(1, parse_pvc_size_to_bytes(model.pvc_size))))

    return total_required, peak_required, entries


def ensure_local_disk_capacity(tenant_catalogs: List[TenantCatalog], args: argparse.Namespace) -> None:
    temp_root = pathlib.Path(tempfile.gettempdir())
    usage = shutil.disk_usage(temp_root)
    total_required, peak_required, entries = estimate_download_space_for_catalogs(tenant_catalogs, args)

    if not entries:
        return

    free_space = usage.free
    required_for_run = total_required
    required_for_peak = peak_required

    # The script downloads models one at a time in the current publisher loop, so the
    # realistic peak is the largest individual model's buffered pvcSize. We still compute
    # the full selected set to surface the larger end-to-end envelope across catalogs.
    if free_space < required_for_peak:
        temp_dir = str(temp_root)
        candidates = [
            f"{tenant}:{model} -> {format_bytes(required)} (pvcSize {format_bytes(base)})"
            for tenant, model, required, base in entries
        ]
        message = (
            "Local disk capacity check failed before model downloads started. "
            f"Temporary download directory: {temp_dir}\n"
            f"Free space: {format_bytes(free_space)}\n"
            f"Required for current peak model: {format_bytes(required_for_peak)}\n"
            f"Required for all selected models in this run: {format_bytes(required_for_run)}\n"
            "Estimated requirement formula: serving.pvcSize * 1.2 (20% buffer).\n"
            "Catalog estimate breakdown:\n" + "\n".join(f"  - {entry}" for entry in candidates) +
            "\n\nRecovery steps:\n"
            "  1) Free enough space on the current temp disk, or set TMPDIR to a larger mounted volume.\n"
            "  2) Reduce the catalog scope with --model or --max-models, or publish fewer tenants at once.\n"
            "  3) Re-run the publish job once free space is available."
        )
        raise SystemExit(message)

    if free_space < required_for_run:
        print(
            f"[WARN] Local disk free space ({format_bytes(free_space)}) is below the full-run estimate "
            f"({format_bytes(required_for_run)}); downloads are still processed one model at a time, so the "
            "sequential peak is the critical threshold."
        )


def upload_file(s3_pool: S3ClientPool, bucket: str, key: str, path: pathlib.Path) -> None:
    s3_pool.call("upload_file", lambda s3: s3.upload_file(str(path), bucket, key))


def prepare_artifact_upload(
    s3_pool: S3ClientPool,
    bucket: str,
    shared_prefix: str,
    local_dir: pathlib.Path,
    file_path: pathlib.Path,
) -> Tuple[str, str, int, str]:
    rel = str(file_path.relative_to(local_dir)).replace("\\", "/")
    sha = file_sha256(file_path)
    size = file_path.stat().st_size
    shared_key = f"{shared_prefix}/{sha}/{rel}"
    if not object_exists(s3_pool, bucket, shared_key):
        upload_file(s3_pool, bucket, shared_key, file_path)
    return rel, sha, size, shared_key


def object_exists(s3_pool: S3ClientPool, bucket: str, key: str) -> bool:
    try:
        s3_pool.call("head_object", lambda s3: s3.head_object(Bucket=bucket, Key=key))
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def load_published_pointer(s3_pool: S3ClientPool, bucket: str, pointer_key: str) -> Optional[Dict[str, object]]:
    try:
        obj = s3_pool.call("get_object(pointer)", lambda s3: s3.get_object(Bucket=bucket, Key=pointer_key))
        return json.loads(obj["Body"].read())
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise


def build_manifest(
    model: ModelConfig,
    revision: str,
    files: List[Tuple[str, str, int, str]],
    run_id: str,
    shared_prefix: str,
) -> Dict[str, object]:
    return {
        "model_name": model.name,
        "source": {
            "type": model.source.source_type,
            "repo": model.source.repo,
            "revision": revision,
        },
        "storage": {
            "prefix": model.storage_prefix,
            "shared_artifacts_prefix": shared_prefix,
            "run_id": run_id,
        },
        "artifacts": [
            {"path": rel, "sha256": sha, "bytes": size, "s3_key": s3_key}
            for rel, sha, size, s3_key in sorted(files, key=lambda x: x[0])
        ],
        "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def resolve_repo_revision(model: ModelConfig) -> str:
    # For now we publish the catalog-specified revision; if desired this can be
    # extended to resolve exact commit SHA via Hugging Face metadata APIs.
    return model.source.revision


def publish_model(
    s3_pool: S3ClientPool,
    storage: StorageConfig,
    model: ModelConfig,
    run_id: str,
    shared_prefix: str,
    hf_max_workers: int,
    upload_max_workers: int,
    force: bool,
) -> None:
    if model.source.source_type.lower() != "huggingface":
        raise ValueError(f"Unsupported source type for {model.name}: {model.source.source_type}")

    revision = resolve_repo_revision(model)
    pointer_key = f"{model.storage_prefix}/manifest.json"
    if not force:
        # revision is expected to be immutable (pinned tag/commit); a moving branch like "main" would never be redetected as changed
        existing = load_published_pointer(s3_pool, storage.bucket, pointer_key)
        if existing and existing.get("source_repo") == model.source.repo and existing.get("source_revision") == revision:
            print(f"[SKIP] {model.name} ({model_identity(model)}) already published; use --force-redownload {model_identity(model)} to republish")
            return

    hf_token = os.environ.get(model.source.auth_env, "")

    with tempfile.TemporaryDirectory(prefix=f"model-{model.name}-") as tmp:
        local_dir = pathlib.Path(tmp) / "download"
        local_dir.mkdir(parents=True, exist_ok=True)

        print(f"[INFO] Downloading {model.name} from {model.source.repo}@{model.source.revision} with HF max-workers={hf_max_workers}")
        snapshot_download(
            repo_id=model.source.repo,
            revision=model.source.revision,
            local_dir=str(local_dir),
            token=hf_token if hf_token else None,
            local_dir_use_symlinks=False,
            max_workers=hf_max_workers,
        )

        files = collect_files(local_dir)
        if not files:
            raise RuntimeError(f"No artifacts found after download for model {model.name}")

        checksummed: List[Tuple[str, str, int, str]] = []
        if upload_max_workers <= 1:
            for file_path in files:
                checksummed.append(prepare_artifact_upload(s3_pool, storage.bucket, shared_prefix, local_dir, file_path))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=upload_max_workers) as executor:
                futures = [
                    executor.submit(prepare_artifact_upload, s3_pool, storage.bucket, shared_prefix, local_dir, file_path)
                    for file_path in files
                ]
                for future in concurrent.futures.as_completed(futures):
                    checksummed.append(future.result())

        manifest = build_manifest(
            model=model,
            revision=revision,
            files=checksummed,
            run_id=run_id,
            shared_prefix=shared_prefix,
        )
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

        run_manifest_key = f"{model.storage_prefix}/manifests/{run_id}.json"
        s3_pool.call(
            "put_object(model-manifest)",
            lambda s3: s3.put_object(Bucket=storage.bucket, Key=run_manifest_key, Body=manifest_bytes, ContentType="application/json"),
        )

        pointer = {
            "active_manifest": run_manifest_key,
            "shared_artifacts_prefix": shared_prefix,
            "model_name": model.name,
            "source_repo": model.source.repo,
            "source_revision": revision,
            "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        pointer_bytes = json.dumps(pointer, indent=2).encode("utf-8")
        s3_pool.call(
            "put_object(manifest-pointer)",
            lambda s3: s3.put_object(Bucket=storage.bucket, Key=pointer_key, Body=pointer_bytes, ContentType="application/json"),
        )

        print(f"[OK] Published model {model.name}. Manifest: s3://{storage.bucket}/{run_manifest_key}")


def filtered_models(args: argparse.Namespace, models: Iterable[ModelConfig]) -> List[ModelConfig]:
    selected = [m for m in models if m.enabled]
    if args.model:
        selected = [m for m in selected if m.name == args.model]
    if args.max_models > 0:
        selected = selected[: args.max_models]
    return selected


def main() -> None:
    args = parse_args()
    storage = parse_storage_config(args)
    tenant_catalogs = load_catalogs(args)
    if not tenant_catalogs:
        raise SystemExit("No catalog inputs were provided")

    force_redownload = set(args.force_redownload)

    ensure_local_disk_capacity(tenant_catalogs, args)

    run_id = args.run_id.strip() or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    s3_pool = s3_client_pool(storage)

    for tenant_catalog in tenant_catalogs:
        catalog_object_key = publish_tenant_catalog(
            s3_pool=s3_pool,
            storage=storage,
            tenant_catalog=tenant_catalog,
            output_prefix=args.published_catalogs_prefix,
            output_key=args.published_catalog_key,
        )
        print(f"[OK] Published tenant catalog for {tenant_catalog.tenant}: s3://{storage.bucket}/{catalog_object_key}")

        selected = filtered_models(args, tenant_catalog.catalog.models)
        if not selected:
            print(f"[INFO] No enabled models selected for tenant {tenant_catalog.tenant}; skipping")
            continue

        print(f"[INFO] Starting publish run {run_id} for tenant {tenant_catalog.tenant} with {len(selected)} model(s)")
        for model in selected:
            publish_model(
                s3_pool=s3_pool,
                storage=storage,
                model=model,
                run_id=run_id,
                shared_prefix=args.shared_artifacts_prefix,
                hf_max_workers=args.hf_max_workers,
                upload_max_workers=args.upload_max_workers,
                force=model_identity(model) in force_redownload,
            )

    print("[OK] Model publication completed")


if __name__ == "__main__":
    main()
