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

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
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


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
