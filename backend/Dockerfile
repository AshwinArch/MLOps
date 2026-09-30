FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY backend/ .
COPY data/ /data/
ENV SEED_DIR=/data
RUN useradd -m -u 10001 app && chown -R app /app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/ready')"
# Migrations are NOT run by default (production: a one-off pre-upgrade Job). Compose sets MIGRATE_ON_START=true.
ENV MIGRATE_ON_START=false AUTO_CREATE_SCHEMA=false
CMD ["sh", "-c", "if [ \"$MIGRATE_ON_START\" = \"true\" ]; then alembic upgrade head; fi && exec uvicorn app.main:get_app --factory --host 0.0.0.0 --port 8000"]
