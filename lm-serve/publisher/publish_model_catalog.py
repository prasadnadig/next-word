#!/usr/bin/env python3
"""Publishes tenant-scoped model artifacts to S3-compatible storage.

This script reads source catalog definitions for one or more tenants, downloads
model artifacts, uploads them to object storage, and writes published tenant
catalog files consumed by the Python model deployment reconciler.

Publication uses shared-artifact deduplication plus manifest-pointer promotion
so serving paths only observe fully published model revisions.

Input mode design (why two modes exist):

1) File mode (`--catalog-tenant-file TENANT=PATH`, repeatable)
     Use when running locally, in CI, or across clusters where tenant catalog
     YAML files are available on disk.
     Example:
         python3 publish_model_catalog.py \
             --catalog-tenant-file dev-west=./catalogs/dev-west.yaml \
             --catalog-tenant-file staging=./catalogs/staging.yaml

2) ConfigMap mode (`--catalog-configmap NAME` + `--catalog-tenant TENANT`, repeatable pairs)
     Use for in-cluster publisher runs where source catalogs are already materialized
     as ConfigMaps and should be fetched from Kubernetes API.
     Example:
         python3 publish_model_catalog.py \
             --catalog-namespace lm-serve \
             --catalog-configmap lm-serve-model-catalog-dev-west \
             --catalog-tenant dev-west \
             --catalog-configmap lm-serve-model-catalog-staging \
             --catalog-tenant staging

The modes are mutually exclusive by design to keep operator intent explicit.

Supported source types:
- huggingface

Catalog expectations:
storage:
  bucket: my-lm-serve-models
  endpoint: https://us-ord-1.linodeobjects.com
  region: us-east-1
models:
  - name: mistral-7b-instruct
    enabled: true
    source:
      type: huggingface
      repo: mistralai/Mistral-7B-Instruct-v0.3
      revision: main
      authEnv: HF_TOKEN
    storage:
      prefix: models/mistral-7b-instruct
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import pathlib
import tempfile
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


@dataclasses.dataclass(frozen=True)
class Catalog:
    storage: StorageConfig
    models: List[ModelConfig]


@dataclasses.dataclass(frozen=True)
class TenantCatalog:
    tenant: str
    namespace: str
    configmap_name: str
    key: str
    catalog: Catalog


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
        self._preferred_index = 0

    def call(self, operation: str, fn):
        ordered_indexes = [self._preferred_index] + [
            i for i in range(len(self._clients)) if i != self._preferred_index
        ]
        last_auth_exc: Optional[Exception] = None
        for index in ordered_indexes:
            s3 = self._clients[index]
            try:
                result = fn(s3)
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
    parser.add_argument("--catalog-tenant-file", action="append", default=[], help="Repeatable tenant=path mapping for tenant catalog files (example: --catalog-tenant-file dev=./dev.yaml)")
    parser.add_argument("--catalog-configmap", action="append", default=[], help="Repeatable source ConfigMap names for ConfigMap mode; each entry must pair with one --catalog-tenant")
    parser.add_argument("--catalog-tenant", action="append", default=[], help="Repeatable tenant labels paired positionally with --catalog-configmap entries")
    parser.add_argument("--catalog-configmap-prefix", default="lm-serve-model-catalog", help="Prefix used for generated tenant-specific ConfigMaps")
    parser.add_argument("--catalog-namespace", default="lm-serve", help="ConfigMap namespace")
    parser.add_argument("--catalog-key", default="models.yaml", help="ConfigMap data key")
    parser.add_argument("--run-id", default="", help="Optional run ID for staging prefix")
    parser.add_argument("--model", default="", help="Optional single model name filter")
    parser.add_argument("--max-models", type=int, default=0, help="Optional cap on number of models to publish")
    parser.add_argument(
        "--prune-staging",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete staging prefix after promotion (default: true; use --no-prune-staging to keep staged objects)",
    )
    parser.add_argument("--shared-artifacts-prefix", default="_shared", help="S3 prefix used for deduplicated artifacts shared across tenant catalogs")
    parser.add_argument("--published-catalogs-prefix", default="published-catalogs", help="Object storage prefix for published per-tenant catalog files")
    parser.add_argument("--published-catalog-key", default="models.yaml", help="Filename written for each published tenant catalog under published-catalogs-prefix")
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


def parse_catalog(text: str) -> Catalog:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise ValueError("Catalog must be a YAML mapping")

    storage_raw = raw.get("storage", {})
    if not isinstance(storage_raw, dict):
        raise ValueError("storage must be a mapping")

    bucket = str(storage_raw.get("bucket", "")).strip()
    endpoint = str(storage_raw.get("endpoint", "")).strip()
    region = str(storage_raw.get("region", "")).strip()

    if not bucket:
        raise ValueError("storage.bucket is required and cannot be empty")
    if not endpoint:
        raise ValueError("storage.endpoint is required and cannot be empty")
    if not region:
        raise ValueError("storage.region is required and cannot be empty")

    parsed_endpoint = urlparse(endpoint)
    if parsed_endpoint.scheme not in {"http", "https"} or not parsed_endpoint.netloc:
        raise ValueError(
            "storage.endpoint must be a valid absolute URL with http/https scheme "
            f"(received: {endpoint!r})"
        )

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,62}", region):
        raise ValueError(
            "storage.region must be 1-63 characters, alphanumeric or hyphen, and start with an alphanumeric "
            f"(received: {region!r})"
        )

    storage = StorageConfig(
        bucket=bucket,
        endpoint=endpoint,
        region=region,
    )

    models_raw = raw.get("models", [])
    if not isinstance(models_raw, list):
        raise ValueError("models must be a list")

    models: List[ModelConfig] = []
    for item in models_raw:
        if not isinstance(item, dict):
            continue

        source_raw = item.get("source", {})
        model = ModelConfig(
            name=str(item["name"]),
            enabled=bool(item.get("enabled", True)),
            source=SourceConfig(
                source_type=str(source_raw.get("type", "huggingface")),
                repo=str(source_raw["repo"]),
                revision=str(source_raw.get("revision", "main")),
                auth_env=str(source_raw.get("authEnv", "HF_TOKEN")),
            ),
            storage_prefix=str(item.get("storage", {}).get("prefix", "")).strip("/"),
        )
        if not model.storage_prefix:
            raise ValueError(f"Model {model.name} missing storage.prefix")
        models.append(model)

    return Catalog(storage=storage, models=models)


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


def catalog_to_tenant_yaml(catalog: Catalog, tenant: str) -> str:
    payload = {
        "storage": {
            "bucket": catalog.storage.bucket,
            "endpoint": catalog.storage.endpoint,
            "region": catalog.storage.region,
        },
        "models": [model_config_to_dict(model) for model in catalog.models],
    }
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def load_catalog_from_file(path: str) -> Catalog:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    if isinstance(parsed, dict) and parsed.get("kind") == "ConfigMap":
        data = parsed.get("data", {})
        if not isinstance(data, dict):
            raise ValueError(f"ConfigMap manifest in {path} does not contain a data map")
        if "models.yaml" not in data:
            raise ValueError(f"ConfigMap manifest in {path} missing data key 'models.yaml'")
        text = str(data["models.yaml"])
    return parse_catalog(text)


def load_catalogs(args: argparse.Namespace) -> List[TenantCatalog]:
    if args.catalog_tenant_file:
        entries: List[TenantCatalog] = []
        for spec in args.catalog_tenant_file:
            tenant, path = parse_tenant_catalog_spec(spec)
            catalog = load_catalog_from_file(path)
            configmap_name = f"{args.catalog_configmap_prefix}-{tenant}"
            entries.append(
                TenantCatalog(
                    tenant=tenant,
                    namespace=args.catalog_namespace,
                    configmap_name=configmap_name,
                    key=args.catalog_key,
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
            catalog = parse_catalog(text)
            entries.append(
                TenantCatalog(
                    tenant=tenant_name.strip(),
                    namespace=args.catalog_namespace,
                    configmap_name=name,
                    key=args.catalog_key,
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


def upload_file(s3_pool: S3ClientPool, bucket: str, key: str, path: pathlib.Path) -> None:
    s3_pool.call("upload_file", lambda s3: s3.upload_file(str(path), bucket, key))


def object_exists(s3_pool: S3ClientPool, bucket: str, key: str) -> bool:
    try:
        s3_pool.call("head_object", lambda s3: s3.head_object(Bucket=bucket, Key=key))
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return False
        raise


def list_keys_under_prefix(s3_pool: S3ClientPool, bucket: str, prefix: str) -> List[str]:
    def _list(s3: BaseClient) -> List[str]:
        keys: List[str] = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    return s3_pool.call("list_objects_v2", _list)


def copy_keys_between_prefixes(
    s3_pool: S3ClientPool,
    bucket: str,
    source_prefix: str,
    destination_prefix: str,
) -> int:
    source_prefix = source_prefix.strip("/") + "/"
    destination_prefix = destination_prefix.strip("/") + "/"

    copied = 0
    for key in list_keys_under_prefix(s3_pool, bucket, source_prefix):
        suffix = key[len(source_prefix) :]
        dest_key = f"{destination_prefix}{suffix}"
        s3_pool.call(
            "copy_object",
            lambda s3: s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=dest_key),
        )
        copied += 1
    return copied


def delete_prefix(s3_pool: S3ClientPool, bucket: str, prefix: str) -> None:
    keys = list_keys_under_prefix(s3_pool, bucket, prefix.strip("/") + "/")
    if not keys:
        return
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i : i + 1000]]
        s3_pool.call("delete_objects", lambda s3: s3.delete_objects(Bucket=bucket, Delete={"Objects": batch}))


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
            "current_prefix": f"{model.storage_prefix}/current",
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
    prune_staging: bool,
    shared_prefix: str,
    shared_uploads: Optional[set] = None,
) -> None:
    if model.source.source_type.lower() != "huggingface":
        raise ValueError(f"Unsupported source type for {model.name}: {model.source.source_type}")

    hf_token = os.environ.get(model.source.auth_env, "")

    with tempfile.TemporaryDirectory(prefix=f"model-{model.name}-") as tmp:
        local_dir = pathlib.Path(tmp) / "download"
        local_dir.mkdir(parents=True, exist_ok=True)

        print(f"[INFO] Downloading {model.name} from {model.source.repo}@{model.source.revision}")
        snapshot_download(
            repo_id=model.source.repo,
            revision=model.source.revision,
            local_dir=str(local_dir),
            token=hf_token if hf_token else None,
            local_dir_use_symlinks=False,
        )

        files = collect_files(local_dir)
        if not files:
            raise RuntimeError(f"No artifacts found after download for model {model.name}")

        checksummed: List[Tuple[str, str, int, str]] = []
        staging_prefix = f"{model.storage_prefix}/_staging/{run_id}"
        shared_uploads = shared_uploads if shared_uploads is not None else set()
        for file_path in files:
            rel = str(file_path.relative_to(local_dir)).replace("\\", "/")
            sha = file_sha256(file_path)
            size = file_path.stat().st_size
            shared_key = f"{shared_prefix}/{sha}/{rel}"
            if not object_exists(s3_pool, storage.bucket, shared_key):
                upload_file(s3_pool, storage.bucket, shared_key, file_path)
                shared_uploads.add(shared_key)
            checksummed.append((rel, sha, size, shared_key))

            staging_key = f"{staging_prefix}/{rel}"
            if prune_staging:
                if not object_exists(s3_pool, storage.bucket, staging_key):
                    upload_file(s3_pool, storage.bucket, staging_key, file_path)

        if prune_staging:
            delete_prefix(s3_pool, storage.bucket, staging_prefix)

        manifest = build_manifest(
            model=model,
            revision=resolve_repo_revision(model),
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
            "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        pointer_bytes = json.dumps(pointer, indent=2).encode("utf-8")
        pointer_key = f"{model.storage_prefix}/manifest.json"
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
    tenant_catalogs = load_catalogs(args)
    if not tenant_catalogs:
        raise SystemExit("No catalog inputs were provided")

    run_id = args.run_id.strip() or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    shared_uploads: set = set()
    s3_pools: Dict[StorageConfig, S3ClientPool] = {}

    for tenant_catalog in tenant_catalogs:
        catalog = tenant_catalog.catalog
        storage = catalog.storage
        if storage not in s3_pools:
            s3_pools[storage] = s3_client_pool(storage)
        s3_pool = s3_pools[storage]

        catalog_object_key = publish_tenant_catalog(
            s3_pool=s3_pool,
            storage=storage,
            tenant_catalog=tenant_catalog,
            output_prefix=args.published_catalogs_prefix,
            output_key=args.published_catalog_key,
        )
        print(f"[OK] Published tenant catalog for {tenant_catalog.tenant}: s3://{storage.bucket}/{catalog_object_key}")

        selected = filtered_models(args, catalog.models)
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
                prune_staging=args.prune_staging,
                shared_prefix=args.shared_artifacts_prefix,
                shared_uploads=shared_uploads,
            )

    print("[OK] Model publication completed")


if __name__ == "__main__":
    main()
