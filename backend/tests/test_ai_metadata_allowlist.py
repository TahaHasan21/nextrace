import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.main import app
from app.models.analysis_run import AnalysisRun
from app.models.event import Event
from app.services.ai.analysis import InvestigationAnalysis
from app.services.ai.context import (
    AI_METADATA_ALLOWLIST,
    AI_METADATA_MAX_STRING_LENGTH,
    build_investigation_context,
    filter_metadata_for_ai,
)
from app.services.ai.prompts import build_user_message
from app.services.ai.provider import get_ai_provider

SENSITIVE_VALUE = "jane.doe@example.com-password-hunter2"

MIXED_METADATA = {
    # allowed
    "version": "1.4.2",
    "failure_count": 7,
    "demo_scenario": "payment-incident-v1",
    # not allowed - each carries the sensitive marker value
    "changed_by": SENSITIVE_VALUE,
    "commit_message": SENSITIVE_VALUE,
    "authorization": SENSITIVE_VALUE,
    "request_body": SENSITIVE_VALUE,
    "exception": SENSITIVE_VALUE,
    # configuration values may be credentials/connection strings
    "previous_value": SENSITIVE_VALUE,
    "new_value": SENSITIVE_VALUE,
}

# The same metadata as an API client may send it: `demo_scenario` is a
# reserved, server-controlled key that POST /events rejects (422).
API_METADATA = {key: value for key, value in MIXED_METADATA.items() if key != "demo_scenario"}


def _event(event_id: int, metadata) -> Event:
    event = Event(
        service="svc",
        environment="prod",
        event_type="incident",
        timestamp=datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc),
        severity="critical",
        source="test",
        message="incident",
        event_metadata=metadata,
        created_at=datetime.now(timezone.utc),
    )
    event.id = event_id
    return event


def test_filter_keeps_only_allowlisted_scalars():
    filtered = filter_metadata_for_ai(MIXED_METADATA)

    assert filtered == {
        "version": "1.4.2",
        "failure_count": 7,
        "demo_scenario": "payment-incident-v1",
    }


def test_filter_drops_nested_values_even_under_allowed_keys():
    filtered = filter_metadata_for_ai(
        {"description": {"secret": SENSITIVE_VALUE}, "status": [SENSITIVE_VALUE], "reason": None}
    )

    assert filtered == {"reason": None}


def test_filter_truncates_long_allowed_strings():
    filtered = filter_metadata_for_ai({"description": "x" * (AI_METADATA_MAX_STRING_LENGTH + 50)})

    assert len(filtered["description"]) == AI_METADATA_MAX_STRING_LENGTH


def test_filter_preserves_none_and_never_mutates_input():
    original = dict(MIXED_METADATA)

    assert filter_metadata_for_ai(None) is None
    assert filter_metadata_for_ai({}) == {}
    filter_metadata_for_ai(original)
    assert original == MIXED_METADATA


def test_allowlist_has_no_identity_or_secret_keys():
    for forbidden in ("changed_by", "user", "email", "authorization", "password", "token",
                      "api_key", "secret", "request_body", "headers", "exception", "stack_trace",
                      "previous_value", "new_value"):
        assert forbidden not in AI_METADATA_ALLOWLIST


def test_context_and_prompt_exclude_non_allowlisted_metadata():
    target = _event(2, MIXED_METADATA)
    timeline = [_event(1, MIXED_METADATA), target]

    context = build_investigation_context(target, timeline, [], [])
    prompt = build_user_message(context)

    assert SENSITIVE_VALUE not in prompt
    assert SENSITIVE_VALUE not in context.model_dump_json()
    assert context.target.metadata == filter_metadata_for_ai(MIXED_METADATA)
    assert all(e.metadata == filter_metadata_for_ai(MIXED_METADATA) for e in context.timeline)
    # The persisted ORM objects are untouched.
    assert target.event_metadata == MIXED_METADATA


class _CapturingProvider:
    model = "fake-model"

    def __init__(self):
        self.contexts = []

    def generate(self, context):
        self.contexts.append(context)
        return InvestigationAnalysis(
            summary="Summary.", uncertainties=["Causation is not established."]
        )


def test_api_path_filters_both_provider_input_and_saved_snapshot(client, db_session):
    response = client.post(
        "/events",
        json={
            "service": "svc",
            "environment": "prod",
            "event_type": "incident",
            "timestamp": "2026-10-20T10:00:00Z",
            "source": "test",
            "message": "incident",
            "metadata": API_METADATA,
        },
    )
    incident = response.json()
    # Stored event keeps its full metadata - filtering applies to the AI path only.
    assert incident["metadata"] == API_METADATA

    provider = _CapturingProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    try:
        analysis = client.post(f"/investigations/{incident['id']}/analysis")
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)
    assert analysis.status_code == 200

    sent_to_provider = provider.contexts[0].model_dump_json()
    assert SENSITIVE_VALUE not in sent_to_provider

    run = db_session.scalars(
        select(AnalysisRun).where(AnalysisRun.target_event_id == incident["id"])
    ).one()
    assert SENSITIVE_VALUE not in json.dumps(run.context_snapshot)
    assert run.context_snapshot["target"]["metadata"] == filter_metadata_for_ai(API_METADATA)

    detail = client.get(f"/investigations/{incident['id']}/analyses/{run.id}")
    assert SENSITIVE_VALUE not in detail.text


def test_seeded_config_change_values_never_reach_ai_or_snapshot(client, db_session):
    """Uses the real demo config_change event (previous_value=20,
    new_value=5): its setting name reaches the AI, its values do not."""
    from app.demo.incident import get_or_create_demo_incident

    events = {event.event_type: event for event in get_or_create_demo_incident(db_session)}
    config_change = events["config_change"]
    assert {"previous_value", "new_value"} <= set(config_change.event_metadata)

    provider = _CapturingProvider()
    app.dependency_overrides[get_ai_provider] = lambda: provider
    try:
        response = client.post(f"/investigations/{events['incident'].id}/analysis")
    finally:
        app.dependency_overrides.pop(get_ai_provider, None)
    assert response.status_code == 200

    sent = next(e for e in provider.contexts[0].timeline if e.event_id == config_change.id)
    assert sent.metadata["config"] == "db_connection_pool"
    assert "previous_value" not in sent.metadata
    assert "new_value" not in sent.metadata
    assert '"previous_value"' not in provider.contexts[0].model_dump_json()

    run = db_session.scalars(
        select(AnalysisRun).where(AnalysisRun.target_event_id == events["incident"].id)
    ).one()
    snapshot = json.dumps(run.context_snapshot)
    assert '"previous_value"' not in snapshot
    assert '"new_value"' not in snapshot
    # The stored demo event itself is untouched.
    db_session.refresh(config_change)
    assert config_change.event_metadata["previous_value"] == 20

