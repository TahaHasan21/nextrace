#!/usr/bin/env python
"""Ingest a reproducible incident scenario through the real API.

Posts a small, realistic event sequence to POST /events - the same
authenticated, idempotent path any producer uses - and prints the IDs,
including the event to investigate. Nothing is written to the database
directly, and nothing marks these events as seeded demo data: they are
ordinary events, clearly labelled `source=scenario-script` so they are
never mistaken for a real integration.

Usage (from backend/; the key is read from a file or NEXTRACE_INGEST_KEY and
is never printed):

    python scripts/ingest_scenario.py deployment-regression --key-file "$VDIR/ingest.key"
    python scripts/ingest_scenario.py db-latency --at 2026-10-24T09:00:00Z

Re-running with the same --at is idempotent: every event carries a
source_event_id derived from (scenario, --at, role), so the existing events
are returned instead of duplicated.

Stdlib only, so it runs with any Python 3.11+.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

SOURCE = "scenario-script"
INGEST_KEY_HEADER = "X-NexTrace-Ingest-Key"
DEFAULT_ENVIRONMENT = "local-demo"

# (role, seconds after --at, event_type, severity, message, metadata)
SCENARIOS: dict[str, dict] = {
    "deployment-regression": {
        "service": "checkout-api",
        "target": "error_spike",
        "events": [
            ("autoscale", -60, "autoscale", "info", "Replica count 3 -> 4", None),
            ("deployment", 0, "deployment", "info", "Version 2.3.0 deployed",
             {"version": "2.3.0", "previous_version": "2.2.9"}),
            ("error_spike", 100, "error_spike", "critical", "HTTP 5xx rate above threshold",
             {"failure_count": 42, "threshold": 10}),
            ("rollback", 240, "rollback", "warning", "Rolled back to 2.2.9",
             {"from_version": "2.3.0", "to_version": "2.2.9"}),
            ("recovery", 390, "recovery", "info", "Checkout error rate back to baseline", None),
        ],
        # Same environment and minutes, different service: must never appear.
        "unrelated": [
            ("other_service", 30, "deployment", "info", "Inventory service deployed", None),
        ],
    },
    "db-latency": {
        "service": "orders-api",
        "target": "error_spike",
        "events": [
            ("db_latency", 0, "db_latency", "warning", "p95 query latency 900 ms",
             {"metric": "database_query_latency_ms", "baseline_ms": 40, "observed_ms": 900,
              "threshold_ms": 250}),
            ("error_spike", 45, "error_spike", "critical", "Upstream request timeouts on /orders",
             {"failure_count": 31}),
        ],
        "unrelated": [],
    },
    "config-regression": {
        "service": "search-api",
        "target": "error_spike",
        "events": [
            ("config_change", 0, "config_change", "info", "search_cache_ttl changed",
             # previous/new values are stored, but never sent to the AI provider.
             {"config": "search_cache_ttl", "previous_value": "300", "new_value": "0"}),
            ("error_spike", 40, "error_spike", "critical", "Search 5xx rate above threshold",
             None),
            ("rollback", 150, "rollback", "warning", "search_cache_ttl reverted",
             {"config": "search_cache_ttl"}),
            ("recovery", 270, "recovery", "info", "Search error rate back to baseline", None),
        ],
        "unrelated": [],
    },
}


def default_base_time(now: datetime | None = None) -> datetime:
    """Eight minutes ago, on the minute: every event (at most +390 s) is in
    the past, and the scenario is the most recent activity in the UI."""
    moment = (now or datetime.now(timezone.utc)) - timedelta(minutes=8)
    return moment.replace(second=0, microsecond=0)


def build_events(
    scenario: str, base: datetime, environment: str = DEFAULT_ENVIRONMENT
) -> list[tuple[str, dict]]:
    """(role, POST /events payload) pairs in chronological order. Pure."""
    spec = SCENARIOS[scenario]
    stamp = base.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rows = [(spec["service"], row) for row in spec["events"]]
    rows += [(f"{spec['service']}-neighbour", row) for row in spec["unrelated"]]

    events = []
    for service, (role, offset, event_type, severity, message, metadata) in rows:
        payload = {
            "service": service,
            "environment": environment,
            "event_type": event_type,
            "timestamp": (base + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z"),
            "severity": severity,
            "source": SOURCE,
            "source_event_id": f"{scenario}:{stamp}:{role}",
            "message": message,
        }
        if metadata is not None:
            payload["metadata"] = metadata
        events.append((role, payload))
    events.sort(key=lambda item: item[1]["timestamp"])
    return events


def _post(base_url: str, key: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/events",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", INGEST_KEY_HEADER: key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def _read_key(key_file: str | None) -> str:
    if key_file:
        with open(key_file, encoding="utf-8") as handle:
            return handle.read().strip()
    return os.environ.get("NEXTRACE_INGEST_KEY", "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--key-file", help="File containing the ingest key (else NEXTRACE_INGEST_KEY).")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    parser.add_argument(
        "--at",
        type=lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")),
        help="Scenario start time, ISO 8601 with a UTC offset (default: 8 minutes ago).",
    )
    args = parser.parse_args()

    key = _read_key(args.key_file)
    if not key:
        print("No ingest key: pass --key-file or set NEXTRACE_INGEST_KEY.", file=sys.stderr)
        return 2
    if args.at is not None and args.at.tzinfo is None:
        print("--at must include a UTC offset, e.g. 2026-10-24T09:00:00Z.", file=sys.stderr)
        return 2
    base = args.at or default_base_time()

    results = {}
    try:
        for role, payload in build_events(args.scenario, base, args.environment):
            body = _post(args.base_url, key, payload)
            results[role] = {"id": body["id"], "created": body["created"]}
    except urllib.error.HTTPError as exc:
        # Status only - the response body is not echoed.
        print(f"POST /events failed with HTTP {exc.code}.", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Could not reach {args.base_url}: {exc.reason}", file=sys.stderr)
        return 1

    target = SCENARIOS[args.scenario]["target"]
    print(json.dumps({
        "scenario": args.scenario,
        "at": base.isoformat().replace("+00:00", "Z"),
        "target_event_id": results[target]["id"],
        "events": results,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
