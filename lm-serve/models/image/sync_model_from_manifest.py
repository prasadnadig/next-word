#!/usr/bin/env python3
"""Sync model artifacts from manifest pointer into a local directory atomically."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import boto3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync model artifacts from manifest pointer")
    parser.add_argument("--bucket", required=True, help="Object storage bucket")
    parser.add_argument("--endpoint", required=True, help="Object storage endpoint URL")
    parser.add_argument("--region", required=True, help="Object storage region")
    parser.add_argument("--model-prefix", required=True, help="Model storage prefix containing manifest pointer")
    parser.add_argument("--output-dir", required=True, help="Local directory for resolved model artifacts")
    parser.add_argument("--manifest-output", default="", help="Optional local path to write active manifest JSON")
    return parser.parse_args()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json_object(s3, bucket: str, key: str) -> dict:
    response = s3.get_object(Bucket=bucket, Key=key)
    payload = response["Body"].read().decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError(f"JSON object expected at s3://{bucket}/{key}")
    return data


def atomic_swap_directory(staging_dir: Path, target_dir: Path) -> None:
    backup_dir = target_dir.parent / f"{target_dir.name}.previous"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    if target_dir.exists():
        target_dir.rename(backup_dir)

    staging_dir.rename(target_dir)

    if backup_dir.exists():
        shutil.rmtree(backup_dir)


def main() -> int:
    args = parse_args()

    model_prefix = args.model_prefix.strip("/")
    pointer_key = f"{model_prefix}/manifest.json"

    s3 = boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        region_name=args.region,
    )

    pointer = load_json_object(s3, args.bucket, pointer_key)
    active_manifest = str(pointer.get("active_manifest", "")).strip()
    if not active_manifest:
        raise SystemExit("manifest pointer missing active_manifest")

    manifest = load_json_object(s3, args.bucket, active_manifest)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise SystemExit("manifest artifacts must be a list")

    target_dir = Path(args.output_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="model-sync-", dir=str(target_dir.parent)) as tmp:
        staging_dir = Path(tmp) / "current"
        staging_dir.mkdir(parents=True, exist_ok=True)

        for item in artifacts:
            if not isinstance(item, dict):
                raise SystemExit("manifest artifact entry must be an object")

            rel = str(item.get("path", "")).strip()
            s3_key = str(item.get("s3_key", "")).strip()
            expected_sha = str(item.get("sha256", "")).strip()

            if not rel or not s3_key or not expected_sha:
                raise SystemExit("manifest artifact missing path/s3_key/sha256")

            dest = staging_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(args.bucket, s3_key, str(dest))

            actual_sha = file_sha256(dest)
            if actual_sha != expected_sha:
                raise SystemExit(f"checksum mismatch for {rel}")

        atomic_swap_directory(staging_dir, target_dir)

    if args.manifest_output:
        manifest_out = Path(args.manifest_output)
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        manifest_out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
