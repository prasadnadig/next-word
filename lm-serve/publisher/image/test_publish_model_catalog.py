import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("publish_model_catalog.py")
SPEC = importlib.util.spec_from_file_location("publish_model_catalog", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_args_accepts_concurrency_flags(monkeypatch):
    argv = [
        "publish_model_catalog.py",
        "--storage-bucket",
        "demo-bucket",
        "--storage-endpoint",
        "https://example.com",
        "--storage-region",
        "us-east-1",
        "--global-catalog-file",
        "./catalogs/models.yaml",
        "--catalog-tenant-file",
        "dev=./catalogs/dev.yaml",
        "--hf-max-workers",
        "11",
        "--upload-max-workers",
        "7",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    args = MODULE.parse_args()

    assert args.hf_max_workers == 11
    assert args.upload_max_workers == 7


def test_parse_args_requires_global_catalog_file_in_file_mode(monkeypatch, capsys):
    argv = [
        "publish_model_catalog.py",
        "--storage-bucket",
        "demo-bucket",
        "--storage-endpoint",
        "https://example.com",
        "--storage-region",
        "us-east-1",
        "--catalog-tenant-file",
        "dev=./catalogs/dev.yaml",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    try:
        MODULE.parse_args()
        assert False, "expected SystemExit"
    except SystemExit:
        pass

    assert "--global-catalog-file is required" in capsys.readouterr().err


GLOBAL_CATALOG_YAML = """
models:
  - name: model-a
    enabled: true
    source:
      type: huggingface
      repo: org/model-a
      revision: abc123
    storage:
      prefix: models/model-a
    serving:
      replicas: 1
      pvcSize: 10Gi
      nodeSelector:
        pool: gpu
  - name: model-b
    enabled: false
    source:
      type: huggingface
      repo: org/model-b
      revision: def456
    storage:
      prefix: models/model-b
    serving:
      replicas: 1
      pvcSize: 20Gi
"""


def test_parse_global_catalog_rejects_duplicate_name():
    duplicate = GLOBAL_CATALOG_YAML + """
  - name: model-a
    source:
      type: huggingface
      repo: org/other
      revision: main
    storage:
      prefix: models/other
"""
    try:
        MODULE.parse_global_catalog(duplicate)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "duplicate model name" in str(exc)


def test_parse_global_catalog_rejects_duplicate_storage_prefix():
    duplicate_prefix = GLOBAL_CATALOG_YAML.replace("models/model-b", "models/model-a")
    try:
        MODULE.parse_global_catalog(duplicate_prefix)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "share storage.prefix" in str(exc)


def test_resolve_tenant_catalog_unknown_model_raises():
    global_catalog = MODULE.parse_global_catalog(GLOBAL_CATALOG_YAML)
    selection = [MODULE.TenantSelectionEntry(name="does-not-exist", enabled=True, serving_overrides={})]
    try:
        MODULE.resolve_tenant_catalog(global_catalog, selection, "tenant-x")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unknown model" in str(exc)


def test_resolve_tenant_catalog_requires_enabled_at_both_levels():
    global_catalog = MODULE.parse_global_catalog(GLOBAL_CATALOG_YAML)
    selection = [
        MODULE.TenantSelectionEntry(name="model-a", enabled=True, serving_overrides={}),
        MODULE.TenantSelectionEntry(name="model-b", enabled=True, serving_overrides={}),
    ]
    catalog = MODULE.resolve_tenant_catalog(global_catalog, selection, "tenant-x")
    resolved = {m.name: m for m in catalog.models}

    assert resolved["model-a"].enabled is True
    # model-b is enabled at the tenant level but globally disabled, so it must stay disabled
    assert resolved["model-b"].enabled is False


def test_resolve_tenant_catalog_applies_serving_overrides():
    global_catalog = MODULE.parse_global_catalog(GLOBAL_CATALOG_YAML)
    selection = [
        MODULE.TenantSelectionEntry(
            name="model-a",
            enabled=True,
            serving_overrides={"replicas": 3, "nodeSelector": {"zone": "us-east-1a"}},
        )
    ]
    catalog = MODULE.resolve_tenant_catalog(global_catalog, selection, "tenant-x")
    model = catalog.models[0]

    assert model.serving["replicas"] == 3
    # deep merge: overriding one nodeSelector key preserves sibling keys from the global block
    assert model.serving["nodeSelector"] == {"pool": "gpu", "zone": "us-east-1a"}
    assert model.pvc_size == "10Gi"
