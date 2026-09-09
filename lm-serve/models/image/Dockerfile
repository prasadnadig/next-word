# Runtime image for running the model deployment reconciler in-cluster.
# Base image choice notes:
# - Option 1: python:3.12.12-slim-bookworm (chosen)
#   - Uses Debian + glibc, matching other Python module images in this repo.
#   - Better compatibility with common Python wheels and enterprise scanners.
#   - Reduces operational drift by keeping one consistent Python runtime family
#     across auth-service, publisher, and model reconciler images.
# - Option 2: python:3.12-alpine (not chosen)
#   - Smaller image footprint, but uses musl libc and Alpine package ecosystem.
#   - Can introduce subtle dependency/tooling differences versus Debian-based
#     images used by other modules, increasing maintenance/debug burden.
#
# Decision: prefer consistency and compatibility over minimal base size for this
# long-lived operational component.
FROM python:3.12.12-slim-bookworm

ARG KUBECTL_VERSION=v1.32.3

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl \
  && rm -rf /var/lib/apt/lists/* \
  && pip install --no-cache-dir pyyaml boto3 \
  && curl -fsSL -o /usr/local/bin/kubectl \
     "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
  && chmod +x /usr/local/bin/kubectl \
  && groupadd --system app \
  && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY deploy_lm_serve_catalog.py /app/deploy_lm_serve_catalog.py
COPY sync_model_from_manifest.py /app/sync_model_from_manifest.py
RUN chmod +x /app/deploy_lm_serve_catalog.py
RUN chmod +x /app/sync_model_from_manifest.py

USER app
ENTRYPOINT ["python3", "/app/deploy_lm_serve_catalog.py"]
