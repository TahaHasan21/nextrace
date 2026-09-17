def _create_event(client, **overrides):
    payload = {
        "service": "payment-service",
        "environment": "production",
        "event_type": "deployment",
        "timestamp": "2026-09-05T10:00:00Z",
        "severity": "info",
        "source": "github-actions",
        "message": "event",
        "metadata": {},
    }
    payload.update(overrides)

    response = client.post("/events", json=payload)
    assert response.status_code == 201
    return response.json()


def test_get_investigation_for_existing_event_returns_200(client):
    target = _create_event(client)

    response = client.get(f"/investigations/{target['id']}")

    assert response.status_code == 200


def test_investigation_returns_target_event(client):
    target = _create_event(client, message="Version 1.4.2 deployed")

    body = client.get(f"/investigations/{target['id']}").json()

    assert body["target_event"]["id"] == target["id"]
    assert body["target_event"]["service"] == "payment-service"
    assert body["target_event"]["environment"] == "production"
    assert body["target_event"]["message"] == "Version 1.4.2 deployed"


def test_investigation_includes_timeline_in_chronological_order(client):
    deployment = _create_event(
        client,
        event_type="deployment",
        timestamp="2026-09-05T10:28:00Z",
        message="deployment",
    )
    error_spike = _create_event(
        client,
        event_type="error_spike",
        timestamp="2026-09-05T10:30:00Z",
        message="error_spike",
    )

    body = client.get(f"/investigations/{error_spike['id']}").json()

    timeline_ids = [event["id"] for event in body["timeline"]]
    assert timeline_ids == [deployment["id"], error_spike["id"]]


def test_investigation_excludes_events_outside_window(client):
    target = _create_event(
        client, event_type="error_spike", timestamp="2026-09-05T10:30:00Z"
    )
    unrelated = _create_event(
        client, event_type="deployment", timestamp="2026-09-05T10:40:00Z"
    )

    body = client.get(f"/investigations/{target['id']}").json()
    timeline_ids = [event["id"] for event in body["timeline"]]

    assert unrelated["id"] not in timeline_ids


def test_investigation_excludes_different_service(client):
    target = _create_event(
        client,
        service="payment-service",
        event_type="error_spike",
        timestamp="2026-09-05T10:30:00Z",
    )
    other_service = _create_event(
        client,
        service="auth-service",
        event_type="deployment",
        timestamp="2026-09-05T10:29:00Z",
    )

    body = client.get(f"/investigations/{target['id']}").json()
    timeline_ids = [event["id"] for event in body["timeline"]]

    assert other_service["id"] not in timeline_ids


def test_investigation_excludes_different_environment(client):
    target = _create_event(
        client,
        environment="production",
        event_type="error_spike",
        timestamp="2026-09-05T10:30:00Z",
    )
    other_environment = _create_event(
        client,
        environment="staging",
        event_type="deployment",
        timestamp="2026-09-05T10:29:00Z",
    )

    body = client.get(f"/investigations/{target['id']}").json()
    timeline_ids = [event["id"] for event in body["timeline"]]

    assert other_environment["id"] not in timeline_ids


def test_investigation_includes_expected_evidence_types(client):
    _create_event(client, event_type="deployment", timestamp="2026-09-05T10:28:00Z")
    error_spike = _create_event(
        client, event_type="error_spike", timestamp="2026-09-05T10:30:00Z"
    )

    body = client.get(f"/investigations/{error_spike['id']}").json()
    evidence_types = {item["type"] for item in body["evidence"]}

    assert "temporal_proximity" in evidence_types
    assert "sequence_relationship" in evidence_types


def test_investigation_evidence_references_correct_event_ids(client):
    _create_event(client, event_type="deployment", timestamp="2026-09-05T10:28:00Z")
    error_spike = _create_event(
        client, event_type="error_spike", timestamp="2026-09-05T10:30:00Z"
    )

    body = client.get(f"/investigations/{error_spike['id']}").json()

    timeline_ids = {event["id"] for event in body["timeline"]}
    assert body["evidence"], "expected at least one evidence item"
    for item in body["evidence"]:
        assert set(item["event_ids"]) <= timeline_ids


def test_investigation_includes_recovery_evidence(client):
    _create_event(client, event_type="error_spike", timestamp="2026-09-05T10:30:00Z")
    rollback = _create_event(
        client, event_type="rollback", timestamp="2026-09-05T10:32:00Z"
    )
    _create_event(client, event_type="recovery", timestamp="2026-09-05T10:33:00Z")

    body = client.get(f"/investigations/{rollback['id']}").json()
    evidence_types = {item["type"] for item in body["evidence"]}

    assert "recovery_relationship" in evidence_types


def test_investigation_for_missing_event_returns_404(client):
    response = client.get("/investigations/999999999")

    assert response.status_code == 404


def test_investigation_for_isolated_event_has_target_only_timeline_and_no_evidence(
    client,
):
    target = _create_event(client, event_type="deployment")

    body = client.get(f"/investigations/{target['id']}").json()

    assert [event["id"] for event in body["timeline"]] == [target["id"]]
    assert body["evidence"] == []


def test_investigation_serializes_all_canonical_fields(client):
    target = _create_event(
        client,
        environment="production",
        source="github-actions",
        metadata={"version": "1.4.2"},
    )

    body = client.get(f"/investigations/{target['id']}").json()

    target_event = body["target_event"]
    assert target_event["environment"] == "production"
    assert target_event["source"] == "github-actions"
    assert target_event["metadata"] == {"version": "1.4.2"}
    assert target_event["created_at"] is not None


def test_investigation_candidates_field_is_a_list(client):
    target = _create_event(client)

    body = client.get(f"/investigations/{target['id']}").json()

    assert isinstance(body["candidates"], list)


def test_investigation_candidate_fields_present(client):
    _create_event(client, event_type="deployment", timestamp="2026-09-05T10:00:00Z")
    incident = _create_event(
        client, event_type="incident", timestamp="2026-09-05T10:02:00Z"
    )

    body = client.get(f"/investigations/{incident['id']}").json()

    assert body["candidates"], "expected at least one candidate"
    candidate = body["candidates"][0]
    assert set(candidate.keys()) == {
        "event_id",
        "event_type",
        "score",
        "reasons",
        "reason_codes",
        "supporting_evidence_ids",
    }


def test_investigation_candidates_ordered_by_score_desc(client):
    _create_event(client, event_type="deployment", timestamp="2026-09-05T10:00:00Z")
    _create_event(client, event_type="config_change", timestamp="2026-09-05T10:03:00Z")
    incident = _create_event(
        client, event_type="incident", timestamp="2026-09-05T10:05:00Z"
    )

    body = client.get(f"/investigations/{incident['id']}").json()
    scores = [candidate["score"] for candidate in body["candidates"]]

    assert scores == sorted(scores, reverse=True)


def test_investigation_excludes_target_and_later_events_from_candidates(client):
    deployment = _create_event(
        client, event_type="deployment", timestamp="2026-09-05T10:00:00Z"
    )
    incident = _create_event(
        client, event_type="incident", timestamp="2026-09-05T10:02:00Z"
    )
    rollback = _create_event(
        client, event_type="rollback", timestamp="2026-09-05T10:05:00Z"
    )

    body = client.get(f"/investigations/{incident['id']}").json()
    candidate_ids = {candidate["event_id"] for candidate in body["candidates"]}

    assert deployment["id"] in candidate_ids
    assert incident["id"] not in candidate_ids
    assert rollback["id"] not in candidate_ids


def test_investigation_candidates_empty_when_no_eligible_candidates(client):
    target = _create_event(client, event_type="incident")

    body = client.get(f"/investigations/{target['id']}").json()

    assert body["candidates"] == []


def test_investigation_demo_incident_candidate_scores_and_order(client, db_session):
    from app.demo.incident import persist_demo_incident

    persisted = persist_demo_incident(db_session)
    incident_event = next(event for event in persisted if event.event_type == "incident")

    body = client.get(f"/investigations/{incident_event.id}").json()

    candidate_types = [candidate["event_type"] for candidate in body["candidates"]]

    # This is a ranking/score check against the explicit V1 rules - not a
    # claim that any one of these events is the confirmed root cause.
    assert candidate_types == [
        "deployment",
        "config_change",
        "db_latency",
        "error_spike",
    ]
    assert "incident" not in candidate_types
    assert "rollback" not in candidate_types
    assert "recovery" not in candidate_types

    scores_by_type = {
        candidate["event_type"]: candidate["score"] for candidate in body["candidates"]
    }
    # Evidence Engine V2 added config_change->db_latency, db_latency->
    # error_spike, and error_spike->incident as supported sequence
    # patterns, which raised db_latency and error_spike's scores (each
    # now earns the full 30-point evidence-relationship contribution).
    assert scores_by_type == {
        "deployment": 90,
        "config_change": 90,
        "db_latency": 80,
        "error_spike": 70,
    }


def test_investigation_candidate_reasons_contain_no_causal_wording(client, db_session):
    from app.demo.incident import persist_demo_incident

    persisted = persist_demo_incident(db_session)
    incident_event = next(event for event in persisted if event.event_type == "incident")

    body = client.get(f"/investigations/{incident_event.id}").json()

    forbidden = ["caused", "resulted in", "responsible", "confirmed root cause"]
    assert body["candidates"], "expected at least one candidate"
    for candidate in body["candidates"]:
        assert candidate["reasons"], "expected at least one reason"
        for reason in candidate["reasons"]:
            lowered = reason.lower()
            for word in forbidden:
                assert word not in lowered


# --- Phase C: Investigation API hardening ---


def test_investigation_response_has_exactly_the_expected_top_level_keys(client):
    target = _create_event(client)

    body = client.get(f"/investigations/{target['id']}").json()

    assert set(body.keys()) == {"target_event", "timeline", "evidence", "candidates"}


def test_investigation_empty_evidence_is_empty_list_not_null(client):
    target = _create_event(client, event_type="deployment")

    response = client.get(f"/investigations/{target['id']}")
    raw_body = response.text

    assert response.json()["evidence"] == []
    # Guard against a `null` slipping through: confirm the raw JSON itself
    # encodes an empty array for "evidence", not a null.
    assert '"evidence": []' in raw_body or '"evidence":[]' in raw_body


def test_investigation_empty_candidates_is_empty_list_not_null(client):
    target = _create_event(client, event_type="incident")

    response = client.get(f"/investigations/{target['id']}")
    raw_body = response.text

    assert response.json()["candidates"] == []
    assert '"candidates": []' in raw_body or '"candidates":[]' in raw_body


def test_investigation_timestamps_are_serialized_as_timezone_aware_iso8601(client):
    target = _create_event(
        client, event_type="deployment", timestamp="2026-09-05T10:00:00Z"
    )

    body = client.get(f"/investigations/{target['id']}").json()
    target_event = body["target_event"]

    for field in ("timestamp", "created_at"):
        value = target_event[field]
        # ISO 8601 with an explicit UTC offset (Pydantic v2 renders this as
        # a trailing "Z" or "+00:00") - never a naive, offset-less string.
        assert value.endswith("Z") or "+" in value or value.count("-") > 2


def test_investigation_candidate_ordering_matches_candidate_engine_directly(
    client, db_session
):
    from app.demo.incident import persist_demo_incident
    from app.models.event import Event
    from app.services.candidates import generate_candidates
    from app.services.evidence import generate_evidence
    from app.services.timeline import build_timeline

    persisted = persist_demo_incident(db_session)
    incident_event = next(event for event in persisted if event.event_type == "incident")

    body = client.get(f"/investigations/{incident_event.id}").json()
    api_order = [candidate["event_id"] for candidate in body["candidates"]]

    target = db_session.get(Event, incident_event.id)
    timeline = build_timeline(db_session, target)
    evidence = generate_evidence(timeline)
    expected = [c.event_id for c in generate_candidates(target, timeline, evidence)]

    # The API must not re-sort or otherwise alter the Candidate Engine's
    # own deterministic ordering.
    assert api_order == expected


def test_investigation_evidence_ordering_is_deterministic_across_requests(
    client, db_session
):
    from app.demo.incident import persist_demo_incident

    persisted = persist_demo_incident(db_session)
    incident_event = next(event for event in persisted if event.event_type == "incident")

    first = client.get(f"/investigations/{incident_event.id}").json()
    second = client.get(f"/investigations/{incident_event.id}").json()

    assert first["evidence"] == second["evidence"]
    assert first["candidates"] == second["candidates"]


def test_investigation_evidence_items_include_stable_versioned_ids(client):
    _create_event(client, event_type="deployment", timestamp="2026-09-05T10:28:00Z")
    error_spike = _create_event(
        client, event_type="error_spike", timestamp="2026-09-05T10:30:00Z"
    )

    body = client.get(f"/investigations/{error_spike['id']}").json()

    assert body["evidence"], "expected at least one evidence item"
    ids = [item["id"] for item in body["evidence"]]
    for evidence_id in ids:
        assert isinstance(evidence_id, str)
        assert evidence_id.startswith("ev1_")
    assert len(ids) == len(set(ids))


def test_investigation_candidates_include_structured_reason_codes(client, db_session):
    from app.demo.incident import persist_demo_incident

    persisted = persist_demo_incident(db_session)
    incident_event = next(event for event in persisted if event.event_type == "incident")

    body = client.get(f"/investigations/{incident_event.id}").json()

    assert body["candidates"], "expected at least one candidate"
    known_codes = {
        "temporal_proximity",
        "relevant_event_type",
        "evidence_sequence",
        "temporal_evidence",
        "recovery_context",
    }
    for candidate in body["candidates"]:
        assert "reason_codes" in candidate
        # Index-aligned with reasons: same length, one code per reason.
        assert len(candidate["reason_codes"]) == len(candidate["reasons"])
        for code in candidate["reason_codes"]:
            assert code in known_codes


def test_investigation_candidate_supporting_evidence_ids_reference_real_evidence_not_events(
    client, db_session
):
    from app.demo.incident import persist_demo_incident

    persisted = persist_demo_incident(db_session)
    incident_event = next(event for event in persisted if event.event_type == "incident")

    body = client.get(f"/investigations/{incident_event.id}").json()

    evidence_ids = {item["id"] for item in body["evidence"]}
    event_ids = {event["id"] for event in body["timeline"]}
    assert body["candidates"], "expected at least one candidate"

    found_any_reference = False
    for candidate in body["candidates"]:
        for reference in candidate["supporting_evidence_ids"]:
            found_any_reference = True
            assert isinstance(reference, str)
            # Every reference resolves to a real evidence item in this
            # investigation's own "evidence" list - never a dangling
            # reference, and never (the old, incorrect semantics) an event id.
            assert reference in evidence_ids
            assert reference not in event_ids
    assert found_any_reference


def test_investigation_openapi_metadata_is_present():
    from app.main import app

    schema = app.openapi()
    operation = schema["paths"]["/investigations/{event_id}"]["get"]

    assert operation.get("summary")
    assert operation.get("description")
