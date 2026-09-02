# Hardened minimal runtime for periodic model publication jobs.
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
