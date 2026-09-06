import datetime
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


API_SERVER = "https://%s:%s" % (
    env("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"),
    env("KUBERNETES_SERVICE_PORT_HTTPS", "443"),
)
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
CA_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

with open(TOKEN_PATH, "r", encoding="utf-8") as fh:
    TOKEN = fh.read().strip()

SSL_CTX = ssl.create_default_context(cafile=CA_PATH)

TARGET_NAMESPACE = env("TARGET_NAMESPACE")
TARGET_CONFIGMAP = env("TARGET_CONFIGMAP")
ENVOY_DEPLOYMENT = env("ENVOY_DEPLOYMENT")

NAMESPACE_ALLOW_REGEX = env("NAMESPACE_ALLOW_REGEX", "^lm-serve-.*$")
SOURCE_CONFIGMAP_NAMES = json.loads(env("SOURCE_CONFIGMAP_NAMES_JSON", "[]"))
SOURCE_LABEL_KEY = env("SOURCE_LABEL_KEY", "lm-serve/model-route-source")
SOURCE_LABEL_VALUE = env("SOURCE_LABEL_VALUE", "true")
SOURCE_KEY = env("SOURCE_KEY", "routes.json")
POLL_INTERVAL_SEC = int(env("POLL_INTERVAL_SEC", "30"))
REQUIRE_ANY_SOURCE = env_bool("REQUIRE_ANY_SOURCE", False)

AUTHZ_MODE = env("AUTHZ_MODE", "internal")
AUTHZ_HOST = env("AUTHZ_HOST")
AUTHZ_NAMESPACE = env("AUTHZ_NAMESPACE")
AUTHZ_PORT = int(env("AUTHZ_PORT", "8080"))
AUTHZ_SCHEME = env("AUTHZ_SCHEME", "http")
AUTHZ_PATH_PREFIX = env("AUTHZ_PATH_PREFIX", "/authorize")
AUTHZ_TIMEOUT = env("AUTHZ_TIMEOUT", "3s")
AUTHZ_ALLOWED_HEADERS = json.loads(env("AUTHZ_ALLOWED_HEADERS_JSON", "[]"))

ENVOY_TARGET_PORT = int(env("ENVOY_TARGET_PORT", "8080"))
ENVOY_HEALTH_PATH = env("ENVOY_HEALTH_PATH", "/healthz")
ENVOY_ADMIN_PORT = int(env("ENVOY_ADMIN_PORT", "9901"))


def request(method: str, path: str, payload: dict | None = None) -> dict:
    url = API_SERVER + path
    body = None
    headers = {
        "Authorization": "Bearer " + TOKEN,
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/merge-patch+json"

    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
        raw = resp.read().decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)


def request_allow_404(method: str, path: str) -> dict | None:
    try:
        return request(method, path)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def list_allowed_namespaces() -> list[str]:
    result = request("GET", "/api/v1/namespaces")
    items = result.get("items", [])
    pattern = re.compile(NAMESPACE_ALLOW_REGEX)
    namespaces = []
    for item in items:
        name = item.get("metadata", {}).get("name", "")
        if pattern.match(name):
            namespaces.append(name)
    return namespaces


def list_source_configmaps() -> list[dict]:
    if not SOURCE_CONFIGMAP_NAMES:
        raise RuntimeError("SOURCE_CONFIGMAP_NAMES_JSON must contain at least one configmap name")

    sources: list[dict] = []
    for ns in list_allowed_namespaces():
        for name in SOURCE_CONFIGMAP_NAMES:
            path = "/api/v1/namespaces/%s/configmaps/%s" % (ns, name)
            cm = request_allow_404("GET", path)
            if cm is None:
                continue
            labels = cm.get("metadata", {}).get("labels", {}) or {}
            if str(labels.get(SOURCE_LABEL_KEY, "")) != SOURCE_LABEL_VALUE:
                continue
            sources.append(cm)
    return sources


def parse_routes_from_source(cm: dict) -> list[dict]:
    ns = cm.get("metadata", {}).get("namespace", "")
    name = cm.get("metadata", {}).get("name", "")
    data = cm.get("data", {}) or {}
    raw = data.get(SOURCE_KEY, "")
    if not raw:
        return []

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON in %s/%s key %s: %s" % (ns, name, SOURCE_KEY, exc))

    routes = parsed.get("routes", []) if isinstance(parsed, dict) else parsed
    if not isinstance(routes, list):
        raise ValueError("routes key must be a list in %s/%s key %s" % (ns, name, SOURCE_KEY))

    out: list[dict] = []
    for idx, route in enumerate(routes):
        if not isinstance(route, dict):
            raise ValueError("route index %d in %s/%s is not an object" % (idx, ns, name))
        for required in ("prefix", "cluster", "serviceHost"):
            if required not in route:
                raise ValueError("route index %d in %s/%s missing %s" % (idx, ns, name, required))
        out.append(route)
    return out


def merge_routes(discovered: list[dict]) -> list[dict]:
    merged = []
    merged.extend(discovered)

    seen_prefix = set()
    cluster_endpoint: dict[str, str] = {}
    for idx, route in enumerate(merged):
        prefix = str(route.get("prefix", ""))
        cluster = str(route.get("cluster", ""))
        service_host = str(route.get("serviceHost", ""))
        service_port = route.get("servicePort", 8000)

        if not prefix or not cluster or not service_host:
            raise ValueError("merged route index %d missing required fields" % idx)

        if prefix in seen_prefix:
            raise ValueError("duplicate route prefix detected: %s" % prefix)
        seen_prefix.add(prefix)

        endpoint = "%s:%s" % (service_host, service_port)
        if cluster in cluster_endpoint and cluster_endpoint[cluster] != endpoint:
            raise ValueError(
                "cluster conflict for %s: %s vs %s" % (cluster, cluster_endpoint[cluster], endpoint)
            )
        cluster_endpoint[cluster] = endpoint

    return merged


def authz_address() -> str:
    if AUTHZ_MODE != "external" and AUTHZ_NAMESPACE:
        return "%s.%s.svc.cluster.local" % (AUTHZ_HOST, AUTHZ_NAMESPACE)
    return AUTHZ_HOST


def render_envoy_config(routes: list[dict]) -> str:
    auth_addr = authz_address()
    auth_uri = "%s://%s:%s" % (AUTHZ_SCHEME, auth_addr, AUTHZ_PORT)

    route_entries: list[dict] = []
    for route in routes:
        route_entries.append(
            {
                "match": {"prefix": str(route["prefix"])},
                "route": {
                    "cluster": str(route["cluster"]),
                    "prefix_rewrite": str(route.get("prefixRewrite", "/")),
                },
            }
        )

    route_entries.append(
        {
            "match": {"path": ENVOY_HEALTH_PATH},
            "direct_response": {
                "status": 200,
                "body": {"inline_string": "ok"},
            },
        }
    )

    if not routes:
        route_entries.append(
            {
                "match": {"prefix": "/"},
                "direct_response": {
                    "status": 503,
                    "body": {
                        "inline_string": "lm-serve platform is up, but no model routes are configured yet"
                    },
                },
            }
        )

    auth_patterns = [{"exact": str(h)} for h in AUTHZ_ALLOWED_HEADERS]
    auth_request = {"allowed_headers": {"patterns": auth_patterns}}
    if AUTHZ_MODE == "external":
        auth_request["headers_to_add"] = [
            {"header": {"key": "authorization", "value": "Bearer %ENVIRONMENT(AUTHZ_BEARER_TOKEN)%"}}
        ]

    auth_cluster: dict = {
        "name": "authz_cluster",
        "type": "STRICT_DNS",
        "connect_timeout": "2s",
        "load_assignment": {
            "cluster_name": "authz_cluster",
            "endpoints": [
                {
                    "lb_endpoints": [
                        {
                            "endpoint": {
                                "address": {
                                    "socket_address": {
                                        "address": auth_addr,
                                        "port_value": AUTHZ_PORT,
                                    }
                                }
                            }
                        }
                    ]
                }
            ],
        },
    }

    if AUTHZ_SCHEME == "https":
        auth_cluster["transport_socket"] = {
            "name": "envoy.transport_sockets.tls",
            "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext",
                "sni": auth_addr,
            },
        }

    seen_clusters = set()
    route_clusters = []
    for route in routes:
        cluster_name = str(route["cluster"])
        if cluster_name in seen_clusters:
            continue
        seen_clusters.add(cluster_name)
        route_clusters.append(
            {
                "name": cluster_name,
                "type": "STRICT_DNS",
                "connect_timeout": "2s",
                "lb_policy": "RING_HASH",
                "load_assignment": {
                    "cluster_name": cluster_name,
                    "endpoints": [
                        {
                            "lb_endpoints": [
                                {
                                    "endpoint": {
                                        "address": {
                                            "socket_address": {
                                                "address": str(route["serviceHost"]),
                                                "port_value": int(route.get("servicePort", 8000)),
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    ]
                },
            }
        )

    config = {
        "static_resources": {
            "listeners": [
                {
                    "name": "ingress_listener",
                    "address": {
                        "socket_address": {
                            "address": "0.0.0.0",
                            "port_value": ENVOY_TARGET_PORT,
                        }
                    },
                    "filter_chains": [
                        {
                            "filters": [
                                {
                                    "name": "envoy.filters.network.http_connection_manager",
                                    "typed_config": {
                                        "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
                                        "stat_prefix": "ingress_http",
                                        "route_config": {
                                            "name": "local_route",
                                            "virtual_hosts": [
                                                {
                                                    "name": "lm_serve_apps",
                                                    "domains": ["*"],
                                                    "routes": route_entries,
                                                }
                                            ],
                                        },
                                        "http_filters": [
                                            {
                                                "name": "envoy.filters.http.ext_authz",
                                                "typed_config": {
                                                    "@type": "type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz",
                                                    "transport_api_version": "V3",
                                                    "failure_mode_allow": False,
                                                    "with_request_body": {
                                                        "max_request_bytes": 8192,
                                                        "allow_partial_message": True,
                                                    },
                                                    "http_service": {
                                                        "server_uri": {
                                                            "uri": auth_uri,
                                                            "cluster": "authz_cluster",
                                                            "timeout": AUTHZ_TIMEOUT,
                                                        },
                                                        "path_prefix": AUTHZ_PATH_PREFIX,
                                                        "authorization_request": auth_request,
                                                    },
                                                },
                                            },
                                            {
                                                "name": "envoy.filters.http.router",
                                                "typed_config": {
                                                    "@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router"
                                                },
                                            },
                                        ],
                                    },
                                }
                            ]
                        }
                    ],
                }
            ],
            "clusters": [auth_cluster] + route_clusters,
        },
        "admin": {
            "address": {
                "socket_address": {
                    "address": "127.0.0.1",
                    "port_value": ENVOY_ADMIN_PORT,
                }
            }
        },
    }

    return json.dumps(config, indent=2, sort_keys=False)


def config_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patch_configmap(config_text: str, cfg_hash: str) -> bool:
    path = "/api/v1/namespaces/%s/configmaps/%s" % (TARGET_NAMESPACE, TARGET_CONFIGMAP)
    current = request("GET", path)
    current_hash = (
        current.get("metadata", {}).get("annotations", {}).get("lm-serve.ai/route-config-hash", "")
    )
    if current_hash == cfg_hash:
        return False

    patch = {
        "metadata": {
            "annotations": {
                "lm-serve.ai/route-config-hash": cfg_hash,
                "lm-serve.ai/route-config-updated-at": datetime.datetime.utcnow().isoformat() + "Z",
            }
        },
        "data": {
            "envoy.yaml": config_text,
        },
    }
    request("PATCH", path, patch)
    return True


def patch_envoy_rollout(cfg_hash: str) -> None:
    path = "/apis/apps/v1/namespaces/%s/deployments/%s" % (TARGET_NAMESPACE, ENVOY_DEPLOYMENT)
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "lm-serve.ai/route-config-hash": cfg_hash,
                        "lm-serve.ai/route-config-updated-at": datetime.datetime.utcnow().isoformat() + "Z",
                    }
                }
            }
        }
    }
    request("PATCH", path, patch)


def run_once() -> None:
    source_cms = list_source_configmaps()
    discovered = []
    for cm in source_cms:
        discovered.extend(parse_routes_from_source(cm))

    if REQUIRE_ANY_SOURCE and len(source_cms) == 0:
        raise RuntimeError(
            "no source ConfigMaps found for names %s in namespaces matching %s with label %s=%s"
            % (SOURCE_CONFIGMAP_NAMES, NAMESPACE_ALLOW_REGEX, SOURCE_LABEL_KEY, SOURCE_LABEL_VALUE)
        )

    merged = merge_routes(discovered)
    cfg = render_envoy_config(merged)
    cfg_hash = config_hash(cfg)

    updated = patch_configmap(cfg, cfg_hash)
    if updated:
        patch_envoy_rollout(cfg_hash)
        print("updated route config hash", cfg_hash, flush=True)
    else:
        print("no route config change", flush=True)


def main() -> int:
    print("route aggregator started", flush=True)
    while True:
        try:
            run_once()
        except Exception as exc:
            print("route aggregator error:", exc, file=sys.stderr, flush=True)
        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
