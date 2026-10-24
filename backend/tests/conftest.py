import os
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

# Tests must never touch the real "nextrace" development database, so they
# run against a dedicated "nextrace_test" database on the same Postgres
# instance instead.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://nextrace:nextrace_dev@localhost:5433/nextrace_test",
)


def _admin_dsn_and_dbname(database_url: str) -> tuple[str, str]:
    parts = urlsplit(database_url)
    db_name = parts.path.lstrip("/")
    admin_parts = parts._replace(scheme="postgresql", path="/postgres")
    return urlunsplit(admin_parts), db_name


def _ensure_test_database_exists(database_url: str) -> None:
    admin_dsn, db_name = _admin_dsn_and_dbname(database_url)
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{db_name}"')


_ensure_test_database_exists(TEST_DATABASE_URL)

from app.core.ingest_auth import INGEST_KEY_ENV_VAR, INGEST_KEY_HEADER  # noqa: E402
from app.core.runtime_events import (  # noqa: E402
    ENABLED_ENV_VAR,
    ENVIRONMENT_ENV_VAR,
    SERVICE_ENV_VAR,
    stop_runtime_telemetry,
)
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models.analysis_run import AnalysisRun  # noqa: E402,F401
from app.models.event import Event  # noqa: E402,F401

test_engine = create_engine(TEST_DATABASE_URL)
TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

# Rebuild the schema from the current models on every test run so the
# isolated test database can never drift from the SQLAlchemy models (e.g.
# after a model gains/loses a column). Safe here only because this engine
# points at the dedicated "nextrace_test" database, never the real one.
Base.metadata.drop_all(bind=test_engine)
Base.metadata.create_all(bind=test_engine)


@pytest.fixture()
def db_session():
    connection = test_engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# Test-only value - never a real credential. POST /events and the AI
# analysis endpoint require it; the shared `client` fixture sends it by
# default so tests that aren't about authentication exercise the same
# authorized request path production callers use.
TEST_INGEST_KEY = "test-ingest-key-not-a-real-secret"


@pytest.fixture(autouse=True)
def _isolated_security_and_telemetry_env(monkeypatch):
    monkeypatch.setenv(INGEST_KEY_ENV_VAR, TEST_INGEST_KEY)
    # A developer's local .env must never switch runtime telemetry on
    # during tests; tests that exercise it configure it explicitly.
    for name in (ENABLED_ENV_VAR, SERVICE_ENV_VAR, ENVIRONMENT_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
    yield
    stop_runtime_telemetry()


@pytest.fixture()
def telemetry_sessions(db_session):
    """Session factory for runtime-telemetry writes: separate sessions, as
    production uses, that still write inside this test's rolled-back
    transaction."""
    connection = db_session.connection()
    return lambda: TestingSessionLocal(bind=connection)


def _test_client(db_session, headers=None):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, headers=headers) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db_session):
    yield from _test_client(db_session, headers={INGEST_KEY_HEADER: TEST_INGEST_KEY})


@pytest.fixture()
def unauthenticated_client(db_session):
    # A separate TestClient instance from `client`, so a test may use both
    # without one's default headers leaking into the other's requests.
    yield from _test_client(db_session)
