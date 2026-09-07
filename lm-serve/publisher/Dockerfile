# Hardened minimal runtime for periodic model publication jobs.
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

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system app && useradd --system --gid app --uid 10001 app

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /app/requirements.txt

COPY publish_model_catalog.py /app/publish_model_catalog.py

USER app
ENTRYPOINT ["python", "/app/publish_model_catalog.py"]
