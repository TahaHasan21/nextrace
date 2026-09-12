# Nextrace backend - FastAPI + SQLAlchemy + Alembic.
# No secrets are baked in; all configuration (DATABASE_URL, AI_PROVIDER,
# AI_API_KEY, AI_MODEL) is supplied at runtime via environment variables.
FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
