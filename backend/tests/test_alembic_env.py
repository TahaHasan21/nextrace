import io
from pathlib import Path

import pytest
import sqlalchemy
from alembic import command, context
from alembic.config import Config

# Runs the real alembic/env.py against a DATABASE_URL whose password
# contains "%" (URL-encoded "@" and "%"). Alembic's Config is a
# ConfigParser with %-interpolation, so env.py must escape the URL when
# storing it - and the URL SQLAlchemy finally receives must be exactly the
# original, unmodified DATABASE_URL. No database connection is made.

PERCENT_DATABASE_URL = (
    "postgresql+psycopg://nextrace:p%40ss%25word@db.example.invalid:5432/nextrace"
)

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"


class _StopBeforeConnect(Exception):
    pass


@pytest.fixture()
def alembic_config(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", PERCENT_DATABASE_URL)
    # No ini file on purpose: env.py then skips fileConfig(), so running it
    # here can't reconfigure logging for the rest of the test session.
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return config


def test_offline_mode_uses_unmodified_database_url_containing_percent(
    alembic_config, monkeypatch
):
    seen_urls = []
    original_configure = context.configure

    def recording_configure(*args, **kwargs):
        seen_urls.append(kwargs.get("url"))
        return original_configure(*args, **kwargs)

    monkeypatch.setattr(context, "configure", recording_configure)
    alembic_config.output_buffer = io.StringIO()

    command.upgrade(alembic_config, "head", sql=True)

    assert seen_urls == [PERCENT_DATABASE_URL]


def test_online_mode_uses_unmodified_database_url_containing_percent(
    alembic_config, monkeypatch
):
    seen_configs = []

    def fake_engine_from_config(configuration, prefix="sqlalchemy.", **kwargs):
        seen_configs.append(configuration)
        raise _StopBeforeConnect

    monkeypatch.setattr(sqlalchemy, "engine_from_config", fake_engine_from_config)

    with pytest.raises(_StopBeforeConnect):
        command.upgrade(alembic_config, "head")

    assert seen_configs[0]["sqlalchemy.url"] == PERCENT_DATABASE_URL
