import inspect
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.normalization.application import normalize_application_event
from app.normalization.github import (
    INGESTABLE_STATES,
    normalize_github_deployment_status_event,
)
from app.normalization.models import CanonicalEvent

# These tests exercise pure Pydantic transforms with no database
# involvement at all - no db_session/client fixture is used anywhere in
# this module, and no real or test PostgreSQL database is touched.


def _github_payload(**overrides):
    payload = {
        "action": "created",
        "deployment_status": {
            "id": 987654321,
            "state": "success",
            "environment": "production",
            "description": "Deployment finished successfully.",
            "created_at": "2026-09-05T10:00:00Z",
        },
        "deployment": {
            "id": 123456789,
            "sha": "abc123",
            "ref": "main",
        },
        "repository": {
            "full_name": "acme/payments-api",
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and key in payload:
            payload[key] = {**payload[key], **value}
        else:
            payload[key] = value
    return payload


APPLICATION_PAYLOAD = {
    "service": "payment-service",
    "environment": "production",
    "event_type": "error_spike",
    "timestamp": "2026-09-05T10:02:41Z",
    "severity": "critical",
    "message": "HTTP 500 rate increased",
    "metadata": {"error_rate": 8.2, "baseline": 0.4},
}


# --- GitHub normalizer (deployment_status) ---


def test_github_valid_payload_normalizes_correctly():
    event = normalize_github_deployment_status_event(_github_payload())

    assert isinstance(event, CanonicalEvent)
    assert event.service == "acme/payments-api"
    assert event.environment == "production"
    assert event.event_type == "deployment"
    assert event.source == "github"
    assert event.severity == "info"
    assert event.message == "Deployment to production: success"


def test_github_repository_full_name_maps_to_service():
    event = normalize_github_deployment_status_event(_github_payload())
    assert event.service == "acme/payments-api"


def test_github_deployment_status_environment_is_preserved():
    event = normalize_github_deployment_status_event(
        _github_payload(deployment_status={"environment": "staging"})
    )
    assert event.environment == "staging"


def test_github_event_type_is_always_deployment_not_the_raw_action():
    event = normalize_github_deployment_status_event(_github_payload(action="created"))
    assert event.event_type == "deployment"


def test_github_source_is_github():
    event = normalize_github_deployment_status_event(_github_payload())
    assert event.source == "github"


def test_github_source_event_id_is_deployment_status_id():
    event = normalize_github_deployment_status_event(
        _github_payload(deployment_status={"id": 555000111})
    )
    assert event.source_event_id == "555000111"


@pytest.mark.parametrize("state", ["failure", "error"])
def test_github_failure_and_error_states_map_to_critical_severity(state):
    event = normalize_github_deployment_status_event(_github_payload(deployment_status={"state": state}))
    assert event.severity == "critical"


def test_github_success_state_maps_to_info_severity():
    event = normalize_github_deployment_status_event(
        _github_payload(deployment_status={"state": "success"})
    )
    assert event.severity == "info"


@pytest.mark.parametrize("state", ["pending", "in_progress", "queued", "inactive"])
def test_github_non_outcome_states_are_rejected_by_the_normalizer(state):
    # The normalizer itself refuses non-ingestable states rather than
    # inventing a severity for them - filtering is the router's job
    # (see app/api/webhooks/github.py), this is just a defensive guard.
    with pytest.raises(ValueError):
        normalize_github_deployment_status_event(_github_payload(deployment_status={"state": state}))


def test_github_ingestable_states_constant_matches_the_three_outcomes():
    assert INGESTABLE_STATES == {"success", "failure", "error"}


def test_github_timestamp_is_parsed_from_deployment_status_created_at():
    event = normalize_github_deployment_status_event(_github_payload())

    assert event.timestamp.tzinfo is not None
    assert event.timestamp == datetime(2026, 9, 5, 10, 0, 0, tzinfo=timezone.utc)


def test_github_message_is_deterministic_from_real_fields():
    event = normalize_github_deployment_status_event(
        _github_payload(deployment_status={"environment": "staging", "state": "failure"})
    )
    assert event.message == "Deployment to staging: failure"


def test_github_metadata_preserves_source_specific_fields():
    event = normalize_github_deployment_status_event(_github_payload())

    assert event.metadata == {
        "repository": "acme/payments-api",
        "sha": "abc123",
        "ref": "main",
        "deployment_id": "123456789",
        "deployment_status_id": "987654321",
        "description": "Deployment finished successfully.",
    }


def test_github_metadata_does_not_contain_the_entire_raw_payload():
    event = normalize_github_deployment_status_event(_github_payload())

    # "repository" is intentionally present as a flat string (the repo's
    # full_name) - what must NOT happen is the raw nested objects (or
    # payload-only fields like action/sender) being copied in wholesale.
    assert isinstance(event.metadata["repository"], str)
    assert "action" not in event.metadata
    assert "sender" not in event.metadata
    assert "deployment" not in event.metadata
    assert "deployment_status" not in event.metadata


def test_github_optional_deployment_fields_can_be_absent():
    payload = _github_payload()
    del payload["deployment"]["sha"]
    del payload["deployment"]["ref"]

    event = normalize_github_deployment_status_event(payload)

    assert event.metadata == {
        "repository": "acme/payments-api",
        "deployment_id": "123456789",
        "deployment_status_id": "987654321",
        "description": "Deployment finished successfully.",
    }


def test_github_optional_description_can_be_absent():
    payload = _github_payload()
    del payload["deployment_status"]["description"]

    event = normalize_github_deployment_status_event(payload)

    assert "description" not in event.metadata


def test_github_missing_repository_fails_validation():
    payload = _github_payload()
    del payload["repository"]

    with pytest.raises(ValidationError):
        normalize_github_deployment_status_event(payload)


def test_github_missing_repository_full_name_fails_validation():
    payload = _github_payload(repository={})
    del payload["repository"]["full_name"]

    with pytest.raises(ValidationError):
        normalize_github_deployment_status_event(payload)


def test_github_missing_deployment_status_fails_validation():
    payload = _github_payload()
    del payload["deployment_status"]

    with pytest.raises(ValidationError):
        normalize_github_deployment_status_event(payload)


def test_github_missing_deployment_status_id_fails_validation():
    payload = _github_payload()
    del payload["deployment_status"]["id"]

    with pytest.raises(ValidationError):
        normalize_github_deployment_status_event(payload)


def test_github_missing_deployment_status_environment_fails_validation():
    payload = _github_payload()
    del payload["deployment_status"]["environment"]

    with pytest.raises(ValidationError):
        normalize_github_deployment_status_event(payload)


def test_github_missing_deployment_status_created_at_fails_validation():
    payload = _github_payload()
    del payload["deployment_status"]["created_at"]

    with pytest.raises(ValidationError):
        normalize_github_deployment_status_event(payload)


def test_github_missing_deployment_fails_validation():
    payload = _github_payload()
    del payload["deployment"]

    with pytest.raises(ValidationError):
        normalize_github_deployment_status_event(payload)


def test_github_missing_deployment_id_fails_validation():
    payload = _github_payload()
    del payload["deployment"]["id"]

    with pytest.raises(ValidationError):
        normalize_github_deployment_status_event(payload)


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
    normalize_github_deployment_status_event(_github_payload())
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
