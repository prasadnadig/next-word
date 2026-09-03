#!/usr/bin/env python3
"""Publishes model artifacts from source to S3-compatible storage.

This script reads the same model catalog used by the model deployment reconciler and pushes
model artifacts to an object-storage staging prefix, then promotes them via a
manifest pointer for stronger consistency guarantees.

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
from typing import Dict, Iterable, List, Optional, Tuple

import boto3
import yaml

from botocore.client import BaseClient
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
class EnvironmentCatalog:
    env: str
    namespace: str
    configmap_name: str
    key: str
    catalog: Catalog


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish model artifacts to S3-compatible storage")
    parser.add_argument("--catalog-file", action="append", default=[], help="Path to a local catalog YAML file; repeat for multiple env-specific catalogs")
    parser.add_argument("--catalog-env-file", action="append", default=[], help="Repeatable env=path mapping for per-env catalogs (example: --catalog-env-file dev=./dev.yaml)")
    parser.add_argument("--env", default="default", help="Environment name used when a single catalog is published")
    parser.add_argument("--catalog-configmap", action="append", default=[], help="ConfigMap name to read; repeat for multiple env-specific catalogs")
    parser.add_argument("--catalog-env", action="append", default=[], help="Environment name corresponding to each --catalog-configmap value")
    parser.add_argument("--catalog-configmap-prefix", default="lm-serve-model-catalog", help="Prefix used for generated env-specific ConfigMaps")
    parser.add_argument("--catalog-namespace", default="lm-serve", help="ConfigMap namespace")
    parser.add_argument("--target-namespace", default="", help="Namespace to write generated per-env catalog ConfigMaps into")
    parser.add_argument("--catalog-key", default="models.yaml", help="ConfigMap data key")
    parser.add_argument("--run-id", default="", help="Optional run ID for staging prefix")
    parser.add_argument("--model", default="", help="Optional single model name filter")
    parser.add_argument("--max-models", type=int, default=0, help="Optional cap on number of models to publish")
    parser.add_argument("--prune-staging", action="store_true", help="Delete staging prefix after promotion")
    parser.add_argument("--shared-artifacts-prefix", default="_shared", help="S3 prefix used for deduplicated artifacts shared across env catalogs")
    parser.add_argument("--write-env-catalogs", action="store_true", help="Create or update per-env catalog ConfigMaps in the target namespace")
    return parser.parse_args()


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
    storage = StorageConfig(
        bucket=str(storage_raw["bucket"]),
        endpoint=str(storage_raw["endpoint"]),
        region=str(storage_raw.get("region", "us-east-1")),
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


def parse_env_catalog_spec(spec: str) -> Tuple[str, str]:
    if "=" in spec:
        env, path = spec.split("=", 1)
    elif ":" in spec:
        env, path = spec.split(":", 1)
    else:
        raise ValueError(f"Invalid catalog env spec '{spec}'. Expected ENV=PATH or ENV:PATH")
    return env.strip(), path.strip()


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


def catalog_to_env_yaml(catalog: Catalog, env: str) -> str:
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


def load_catalogs(args: argparse.Namespace) -> List[EnvironmentCatalog]:
    if args.catalog_env_file:
        entries: List[EnvironmentCatalog] = []
        for spec in args.catalog_env_file:
            env, path = parse_env_catalog_spec(spec)
            catalog = load_catalog_from_file(path)
            namespace = args.target_namespace or args.catalog_namespace
            configmap_name = f"{args.catalog_configmap_prefix}-{env}"
            entries.append(
                EnvironmentCatalog(
                    env=env,
                    namespace=namespace,
                    configmap_name=configmap_name,
                    key=args.catalog_key,
                    catalog=catalog,
                )
            )
        return entries

    if args.catalog_file:
        if len(args.catalog_file) > 1:
            raise ValueError("Use --catalog-env-file ENV=PATH for multiple catalogs. The single --catalog-file path is for one catalog only.")
        catalog = load_catalog_from_file(args.catalog_file[0])
        namespace = args.target_namespace or args.catalog_namespace
        configmap_name = (args.catalog_configmap[0] if args.catalog_configmap else f"{args.catalog_configmap_prefix}-{args.env}")
        return [
            EnvironmentCatalog(
                env=args.env,
                namespace=namespace,
                configmap_name=configmap_name,
                key=args.catalog_key,
                catalog=catalog,
            )
        ]

    if args.catalog_configmap:
        if args.catalog_env and len(args.catalog_env) != len(args.catalog_configmap):
            raise ValueError("The number of --catalog-env values must match the number of --catalog-configmap values")

        names = args.catalog_configmap
        env_names = args.catalog_env or [args.env for _ in names]
        entries: List[EnvironmentCatalog] = []
        for name, env_name in zip(names, env_names):
            text = load_catalog_text_from_configmap(
                namespace=args.catalog_namespace,
                name=name,
                key=args.catalog_key,
            )
            catalog = parse_catalog(text)
            entries.append(
                EnvironmentCatalog(
                    env=env_name,
                    namespace=args.target_namespace or args.catalog_namespace,
                    configmap_name=name,
                    key=args.catalog_key,
                    catalog=catalog,
                )
            )
        return entries

    default_configmap_name = "lm-serve-model-catalog"
    text = load_catalog_text_from_configmap(
        namespace=args.catalog_namespace,
        name=default_configmap_name,
        key=args.catalog_key,
    )
    catalog = parse_catalog(text)
    return [
        EnvironmentCatalog(
            env=args.env,
            namespace=args.target_namespace or args.catalog_namespace,
            configmap_name=default_configmap_name,
            key=args.catalog_key,
            catalog=catalog,
        )
    ]


def upsert_configmap(namespace: str, name: str, key: str, value: str) -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    api = client.CoreV1Api()
    try:
        api.read_namespaced_config_map(name=name, namespace=namespace)
        api.patch_namespaced_config_map(
            name=name,
            namespace=namespace,
            body=client.V1ConfigMap(
                metadata=client.V1ObjectMeta(name=name, namespace=namespace),
                data={key: value},
            ),
        )
    except client.exceptions.ApiException as exc:
        if exc.status == 404:
            api.create_namespaced_config_map(
                namespace=namespace,
                body=client.V1ConfigMap(
                    metadata=client.V1ObjectMeta(name=name, namespace=namespace),
                    data={key: value},
                ),
            )
        else:
            raise


def write_env_catalogs(env_catalogs: Iterable[EnvironmentCatalog]) -> None:
    for env_catalog in env_catalogs:
        rendered = catalog_to_env_yaml(env_catalog.catalog, env_catalog.env)
        upsert_configmap(
            namespace=env_catalog.namespace,
            name=env_catalog.configmap_name,
            key=env_catalog.key,
            value=rendered,
        )


def s3_client(storage: StorageConfig) -> BaseClient:
    access_key = os.environ.get("S3_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("S3_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY")

    if not access_key or not secret_key:
        raise ValueError("Missing S3 credentials in env (S3_ACCESS_KEY_ID/S3_SECRET_ACCESS_KEY)")

    return boto3.client(
        "s3",
        endpoint_url=storage.endpoint,
        region_name=storage.region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def file_sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files(root: pathlib.Path) -> List[pathlib.Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def upload_file(s3: BaseClient, bucket: str, key: str, path: pathlib.Path) -> None:
    s3.upload_file(str(path), bucket, key)


def list_keys_under_prefix(s3: BaseClient, bucket: str, prefix: str) -> List[str]:
    keys: List[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def copy_keys_between_prefixes(
    s3: BaseClient,
    bucket: str,
    source_prefix: str,
    destination_prefix: str,
) -> int:
    source_prefix = source_prefix.strip("/") + "/"
    destination_prefix = destination_prefix.strip("/") + "/"

    copied = 0
    for key in list_keys_under_prefix(s3, bucket, source_prefix):
        suffix = key[len(source_prefix) :]
        dest_key = f"{destination_prefix}{suffix}"
        s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=dest_key)
        copied += 1
    return copied


def delete_prefix(s3: BaseClient, bucket: str, prefix: str) -> None:
    keys = list_keys_under_prefix(s3, bucket, prefix.strip("/") + "/")
    if not keys:
        return
    for i in range(0, len(keys), 1000):
        batch = [{"Key": k} for k in keys[i : i + 1000]]
        s3.delete_objects(Bucket=bucket, Delete={"Objects": batch})


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
    s3: BaseClient,
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
            try:
                s3.head_object(Bucket=storage.bucket, Key=shared_key)
            except Exception:
                upload_file(s3, storage.bucket, shared_key, file_path)
                shared_uploads.add(shared_key)
            checksummed.append((rel, sha, size, shared_key))

            staging_key = f"{staging_prefix}/{rel}"
            if prune_staging:
                try:
                    s3.head_object(Bucket=storage.bucket, Key=staging_key)
                except Exception:
                    upload_file(s3, storage.bucket, staging_key, file_path)

        if prune_staging:
            delete_prefix(s3, storage.bucket, staging_prefix)

        manifest = build_manifest(
            model=model,
            revision=resolve_repo_revision(model),
            files=checksummed,
            run_id=run_id,
            shared_prefix=shared_prefix,
        )
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

        run_manifest_key = f"{model.storage_prefix}/manifests/{run_id}.json"
        s3.put_object(Bucket=storage.bucket, Key=run_manifest_key, Body=manifest_bytes, ContentType="application/json")

        pointer = {
            "active_manifest": run_manifest_key,
            "shared_artifacts_prefix": shared_prefix,
            "model_name": model.name,
            "published_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        pointer_bytes = json.dumps(pointer, indent=2).encode("utf-8")
        pointer_key = f"{model.storage_prefix}/manifest.json"
        s3.put_object(Bucket=storage.bucket, Key=pointer_key, Body=pointer_bytes, ContentType="application/json")

        print(f"[OK] Published model {model.name}. Manifest: s3://{storage.bucket}/{run_manifest_key}")


def load_catalog(args: argparse.Namespace) -> Catalog:
    if args.catalog_file:
        text = pathlib.Path(args.catalog_file).read_text(encoding="utf-8")
        parsed = yaml.safe_load(text)
        if isinstance(parsed, dict) and parsed.get("kind") == "ConfigMap":
            data = parsed.get("data", {})
            if not isinstance(data, dict) or args.catalog_key not in data:
                raise ValueError(
                    f"ConfigMap manifest in {args.catalog_file} does not contain data key {args.catalog_key}"
                )
            text = str(data[args.catalog_key])
    else:
        text = load_catalog_text_from_configmap(
            namespace=args.catalog_namespace,
            name=args.catalog_configmap,
            key=args.catalog_key,
        )
    return parse_catalog(text)


def filtered_models(args: argparse.Namespace, models: Iterable[ModelConfig]) -> List[ModelConfig]:
    selected = [m for m in models if m.enabled]
    if args.model:
        selected = [m for m in selected if m.name == args.model]
    if args.max_models > 0:
        selected = selected[: args.max_models]
    return selected


def main() -> None:
    args = parse_args()
    env_catalogs = load_catalogs(args)
    if not env_catalogs:
        raise SystemExit("No catalog inputs were provided")

    primary_catalog = env_catalogs[0].catalog
    s3 = s3_client(primary_catalog.storage)

    if args.write_env_catalogs:
        target_namespace = args.target_namespace or args.catalog_namespace
        for env_catalog in env_catalogs:
            env_catalog = dataclasses.replace(env_catalog, namespace=target_namespace)
            write_env_catalogs([env_catalog])

    run_id = args.run_id.strip() or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    shared_uploads: set = set()

    for env_catalog in env_catalogs:
        catalog = env_catalog.catalog
        selected = filtered_models(args, catalog.models)
        if not selected:
            print(f"[INFO] No enabled models selected for env {env_catalog.env}; skipping")
            continue

        print(f"[INFO] Starting publish run {run_id} for env {env_catalog.env} with {len(selected)} model(s)")
        for model in selected:
            publish_model(
                s3=s3,
                storage=catalog.storage,
                model=model,
                run_id=run_id,
                prune_staging=args.prune_staging,
                shared_prefix=args.shared_artifacts_prefix,
                shared_uploads=shared_uploads,
            )

    print("[OK] Model publication completed")


if __name__ == "__main__":
    main()
