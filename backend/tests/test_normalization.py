import inspect
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.normalization.application import normalize_application_event
from app.normalization.github import normalize_github_event
from app.normalization.models import CanonicalEvent

# These tests exercise pure Pydantic transforms with no database
# involvement at all - no db_session/client fixture is used anywhere in
# this module, and no real or test PostgreSQL database is touched.

GITHUB_PAYLOAD = {
    "action": "deployment",
    "repository": "payments-api",
    "environment": "production",
    "sha": "abc123",
    "ref": "main",
    "deployment_id": "dep-42",
    "timestamp": "2026-09-05T10:00:00Z",
}

APPLICATION_PAYLOAD = {
    "service": "payment-service",
    "environment": "production",
    "event_type": "error_spike",
    "timestamp": "2026-09-05T10:02:41Z",
    "severity": "critical",
    "message": "HTTP 500 rate increased",
    "metadata": {"error_rate": 8.2, "baseline": 0.4},
}


# --- GitHub normalizer ---


def test_github_valid_payload_normalizes_correctly():
    event = normalize_github_event(GITHUB_PAYLOAD)

    assert isinstance(event, CanonicalEvent)
    assert event.service == "payments-api"
    assert event.environment == "production"
    assert event.event_type == "deployment"
    assert event.source == "github"
    assert event.severity == "info"
    assert event.message == "Deployment of payments-api"


def test_github_repository_maps_to_service():
    event = normalize_github_event(GITHUB_PAYLOAD)
    assert event.service == GITHUB_PAYLOAD["repository"]


def test_github_environment_is_preserved():
    event = normalize_github_event(GITHUB_PAYLOAD)
    assert event.environment == GITHUB_PAYLOAD["environment"]


def test_github_action_maps_to_event_type():
    event = normalize_github_event(GITHUB_PAYLOAD)
    assert event.event_type == GITHUB_PAYLOAD["action"]


def test_github_source_is_github():
    event = normalize_github_event(GITHUB_PAYLOAD)
    assert event.source == "github"


def test_github_deployment_severity_is_info():
    event = normalize_github_event(GITHUB_PAYLOAD)
    assert event.severity == "info"


def test_github_timestamp_is_parsed_and_timezone_aware():
    event = normalize_github_event(GITHUB_PAYLOAD)

    assert event.timestamp.tzinfo is not None
    assert event.timestamp == datetime(2026, 9, 5, 10, 0, 0, tzinfo=timezone.utc)


def test_github_metadata_preserves_source_specific_fields():
    event = normalize_github_event(GITHUB_PAYLOAD)

    assert event.metadata == {
        "repository": "payments-api",
        "sha": "abc123",
        "ref": "main",
        "deployment_id": "dep-42",
    }


def test_github_missing_repository_fails_validation():
    payload = dict(GITHUB_PAYLOAD)
    del payload["repository"]

    with pytest.raises(ValidationError):
        normalize_github_event(payload)


def test_github_missing_environment_fails_validation():
    payload = dict(GITHUB_PAYLOAD)
    del payload["environment"]

    with pytest.raises(ValidationError):
        normalize_github_event(payload)


def test_github_missing_action_fails_validation():
    payload = dict(GITHUB_PAYLOAD)
    del payload["action"]

    with pytest.raises(ValidationError):
        normalize_github_event(payload)


def test_github_missing_timestamp_fails_validation():
    payload = dict(GITHUB_PAYLOAD)
    del payload["timestamp"]

    with pytest.raises(ValidationError):
        normalize_github_event(payload)


def test_github_optional_metadata_fields_can_be_absent():
    payload = {
        "action": "deployment",
        "repository": "payments-api",
        "environment": "production",
        "timestamp": "2026-09-05T10:00:00Z",
    }

    event = normalize_github_event(payload)

    assert event.metadata == {"repository": "payments-api"}


# --- Application normalizer ---


def test_application_valid_payload_normalizes_correctly():
    event = normalize_application_event(APPLICATION_PAYLOAD)

    assert event.service == "payment-service"
    assert event.environment == "production"
    assert event.event_type == "error_spike"
    assert event.severity == "critical"
    assert event.message == "HTTP 500 rate increased"


def test_application_source_is_application():
    event = normalize_application_event(APPLICATION_PAYLOAD)
    assert event.source == "application"


def test_application_metadata_is_preserved():
    event = normalize_application_event(APPLICATION_PAYLOAD)
    assert event.metadata == {"error_rate": 8.2, "baseline": 0.4}


def test_application_timestamp_remains_timezone_aware():
    event = normalize_application_event(APPLICATION_PAYLOAD)

    assert event.timestamp.tzinfo is not None
    assert event.timestamp == datetime(2026, 9, 5, 10, 2, 41, tzinfo=timezone.utc)


def test_application_missing_required_field_fails_validation():
    payload = dict(APPLICATION_PAYLOAD)
    del payload["service"]

    with pytest.raises(ValidationError):
        normalize_application_event(payload)


# --- Canonical model shape ---


def test_canonical_event_contains_only_intended_fields():
    assert set(CanonicalEvent.model_fields.keys()) == {
        "service",
        "environment",
        "event_type",
        "timestamp",
        "severity",
        "source",
        "source_event_id",
        "message",
        "metadata",
    }


def test_canonical_event_has_no_database_specific_fields():
    event = normalize_application_event(APPLICATION_PAYLOAD)

    assert not hasattr(event, "id")
    assert not hasattr(event, "created_at")


# --- Isolation from persistence ---


def test_normalization_does_not_require_database_session():
    # No db session/fixture is used here - these calls succeed with zero
    # database involvement.
    normalize_github_event(GITHUB_PAYLOAD)
    normalize_application_event(APPLICATION_PAYLOAD)


def test_normalization_modules_do_not_import_sqlalchemy_or_orm():
    import app.normalization.application as application_module
    import app.normalization.github as github_module
    import app.normalization.models as models_module

    for module in (models_module, github_module, application_module):
        source = inspect.getsource(module)
        assert "sqlalchemy" not in source.lower()
        assert "app.models" not in source
        assert "app.db" not in source
