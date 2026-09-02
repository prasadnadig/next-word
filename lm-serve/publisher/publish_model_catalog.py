#!/usr/bin/env python3
"""Publishes model artifacts from source to S3-compatible storage.

This script reads the same model catalog used by the vLLM deployer and pushes
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish model artifacts to S3-compatible storage")
    parser.add_argument("--catalog-file", help="Path to local catalog YAML file")
    parser.add_argument("--catalog-configmap", default="lm-serve-model-catalog", help="ConfigMap name")
    parser.add_argument("--catalog-namespace", default="lm-serve", help="ConfigMap namespace")
    parser.add_argument("--catalog-key", default="models.yaml", help="ConfigMap data key")
    parser.add_argument("--run-id", default="", help="Optional run ID for staging prefix")
    parser.add_argument("--model", default="", help="Optional single model name filter")
    parser.add_argument("--max-models", type=int, default=0, help="Optional cap on number of models to publish")
    parser.add_argument("--prune-staging", action="store_true", help="Delete staging prefix after promotion")
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


def build_manifest(model: ModelConfig, revision: str, files: List[Tuple[str, str, int]], run_id: str) -> Dict[str, object]:
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
            "run_id": run_id,
        },
        "artifacts": [
            {"path": rel, "sha256": sha, "bytes": size}
            for rel, sha, size in sorted(files, key=lambda x: x[0])
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

        checksummed: List[Tuple[str, str, int]] = []
        staging_prefix = f"{model.storage_prefix}/_staging/{run_id}"
        for file_path in files:
            rel = str(file_path.relative_to(local_dir)).replace("\\", "/")
            sha = file_sha256(file_path)
            size = file_path.stat().st_size
            checksummed.append((rel, sha, size))

            key = f"{staging_prefix}/{rel}"
            upload_file(s3, storage.bucket, key, file_path)

        staged_keys = list_keys_under_prefix(s3, storage.bucket, f"{staging_prefix}/")
        if len(staged_keys) != len(checksummed):
            raise RuntimeError(
                f"Staging verification failed for {model.name}: expected {len(checksummed)} objects, found {len(staged_keys)}"
            )

        print(f"[INFO] Promoting staged artifacts for {model.name} to current/")
        current_prefix = f"{model.storage_prefix}/current"
        copied = copy_keys_between_prefixes(
            s3=s3,
            bucket=storage.bucket,
            source_prefix=staging_prefix,
            destination_prefix=current_prefix,
        )
        if copied != len(checksummed):
            raise RuntimeError(
                f"Promotion copy mismatch for {model.name}: copied {copied}, expected {len(checksummed)}"
            )

        manifest = build_manifest(
            model=model,
            revision=resolve_repo_revision(model),
            files=checksummed,
            run_id=run_id,
        )
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

        run_manifest_key = f"{model.storage_prefix}/manifests/{run_id}.json"
        s3.put_object(Bucket=storage.bucket, Key=run_manifest_key, Body=manifest_bytes, ContentType="application/json")

        pointer = {
            "active_manifest": run_manifest_key,
            "current_prefix": current_prefix,
            "model_name": model.name,
            "promoted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        pointer_bytes = json.dumps(pointer, indent=2).encode("utf-8")
        pointer_key = f"{model.storage_prefix}/manifest.json"
        s3.put_object(Bucket=storage.bucket, Key=pointer_key, Body=pointer_bytes, ContentType="application/json")

        if prune_staging:
            delete_prefix(s3, storage.bucket, staging_prefix)

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
    catalog = load_catalog(args)
    s3 = s3_client(catalog.storage)

    selected = filtered_models(args, catalog.models)
    if not selected:
        raise SystemExit("No enabled models selected for publishing")

    run_id = args.run_id.strip() or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]

    print(f"[INFO] Starting publish run {run_id} for {len(selected)} model(s)")
    for model in selected:
        publish_model(
            s3=s3,
            storage=catalog.storage,
            model=model,
            run_id=run_id,
            prune_staging=args.prune_staging,
        )

    print("[OK] Model publication completed")


if __name__ == "__main__":
    main()
