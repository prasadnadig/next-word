FROM python:3.12-slim

WORKDIR /app

COPY aggregate_routes.py /app/aggregate_routes.py

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

USER 10001:10001

CMD ["python", "/app/aggregate_routes.py"]
