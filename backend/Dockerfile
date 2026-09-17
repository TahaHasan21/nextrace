# Nextrace backend - FastAPI + SQLAlchemy + Alembic.
# No secrets are baked in; all configuration (DATABASE_URL, AI_PROVIDER,
# AI_API_KEY, AI_MODEL) is supplied at runtime via environment variables.
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Dedicated non-root application user - the app never needs root at
# runtime (it binds to port 8000, well above the privileged-port range,
# and writes to no local files: all persistence is PostgreSQL). Created
# before the COPY --chown steps below so those can reference it directly.
RUN groupadd --system nextrace && useradd --system --no-create-home --gid nextrace nextrace

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=nextrace:nextrace app ./app
COPY --chown=nextrace:nextrace alembic ./alembic
COPY --chown=nextrace:nextrace alembic.ini .
COPY --chown=nextrace:nextrace docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

USER nextrace

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
