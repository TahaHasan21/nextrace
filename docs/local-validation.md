# Local end-to-end validation with genuine runtime events

Demonstrates the full pipeline on events produced by a **running NexTrace
backend**, with no seeded demo data and nothing inserted by hand:

runtime start → `deployment` event → real application failures → automatic
`error_spike` → correlation → timeline → evidence → ranked candidate →
(optional) AI explanation.

It then adds three reproducible incident scenarios through the real
`POST /events` API (step 9), a genuine unhandled-exception spike (step 10),
and the investigation UI (step 11).

Everything runs locally against a throwaway database. No AWS resources, no
production data, and - except for the optional AI-explanation step - no
credentials or paid API calls.

**Never print the ingest key or any provider key.** The commands below keep
the ingest key in a mode-600 file and pass it to `curl` via a header file.

## Prerequisites

- Docker (any engine; e.g. `export DOCKER_CONTEXT=default` for the native
  engine if Docker Desktop isn't running).
- `backend/.venv` (Python 3.11+) with `backend/requirements.txt` installed.
- For the UI: `frontend/node_modules` installed (`npm install --legacy-peer-deps`).
- Free local ports 55436 (PostgreSQL), 8000 (backend) and 4210 (UI).

All commands run from the repository root in **one shell**, unless noted.

## 1. Isolated PostgreSQL (throwaway)

```bash
export VDIR=/tmp/nextrace-validation && mkdir -p -m 700 "$VDIR"
docker run -d --rm --name nextrace-validation-pg \
  -e POSTGRES_USER=nextrace -e POSTGRES_PASSWORD=nextrace_dev \
  -e POSTGRES_DB=nextrace_validation -p 127.0.0.1:55436:5432 postgres:17
until docker exec nextrace-validation-pg pg_isready -U nextrace -h 127.0.0.1 >/dev/null 2>&1; do sleep 1; done
```

## 2. Apply the real migrations

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://nextrace:nextrace_dev@127.0.0.1:55436/nextrace_validation
.venv/bin/alembic upgrade head      # ends at the current head revision; the database starts empty
```

## 3. Ingest key and local runtime telemetry

```bash
( umask 077
  python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$VDIR/ingest.key"
  printf 'X-NexTrace-Ingest-Key: %s\n' "$(cat "$VDIR/ingest.key")" > "$VDIR/key.header" )
export NEXTRACE_INGEST_KEY="$(cat "$VDIR/ingest.key")"
export NEXTRACE_RUNTIME_TELEMETRY_ENABLED=true \
       NEXTRACE_RUNTIME_SERVICE=nextrace-backend \
       NEXTRACE_RUNTIME_ENVIRONMENT=local-validation \
       NEXTRACE_RUNTIME_LOCAL_IDENTITY=true \
       NEXTRACE_RUNTIME_INSTANCE_ID=validation-run-1
```

`local-validation` keeps these events apart from any other environment, and
`NEXTRACE_RUNTIME_LOCAL_IDENTITY` is ignored on ECS (see the README's
_Runtime telemetry_ section).

## 4. Start NexTrace with a controlled, local-only fault

The AI provider is pointed at a closed local port, so every analysis request
fails with a genuine provider connection error inside the real code path.
Nothing leaves the machine and the placeholder is not a credential. These
variables are set **only for this process**, not exported.

```bash
AI_PROVIDER=openai AI_API_KEY=placeholder-not-a-key AI_MODEL=fault-injection \
OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$VDIR/backend-fault.log" 2>&1 &
echo $! > "$VDIR/backend.pid"
until curl -sf http://127.0.0.1:8000/health >/dev/null; do sleep 0.5; done
```

## 5. Capture the genuine startup event

The startup event is written on a background thread just after the backend
starts, so wait for it rather than assuming it is already there:

```bash
until curl -s "http://127.0.0.1:8000/events?source=nextrace-runtime&environment=local-validation" | grep -q '"deployment"'; do sleep 0.5; done
curl -s "http://127.0.0.1:8000/events?source=nextrace-runtime&environment=local-validation" | python3 -m json.tool
STARTUP_ID=$(curl -s "http://127.0.0.1:8000/events?source=nextrace-runtime&environment=local-validation" \
  | python3 -c 'import sys,json; print(next(e["id"] for e in json.load(sys.stdin) if e["event_type"]=="deployment"))')
```

Expect one `deployment` event with `source_event_id`
`local/validation-run-1:startup` and metadata `identity_source: "local"`.

## 6. Trigger the failure (within 5 minutes of startup)

Each request is a real, authenticated analysis call that the misconfigured
provider fails (a few seconds each, as the client retries the connection);
the default threshold is 5 failures in 60 seconds.

```bash
curl -s -o /dev/null -w 'unauthenticated -> %{http_code}\n' -X POST "http://127.0.0.1:8000/investigations/$STARTUP_ID/analysis"   # 401
for i in 1 2 3 4 5; do
  curl -s -o /dev/null -w "failure $i -> %{http_code}\n" -H @"$VDIR/key.header" \
    -X POST "http://127.0.0.1:8000/investigations/$STARTUP_ID/analysis"                           # 503 each
done
FAILURES_DONE=$(date +%s)   # used by the optional step 10
```

The 5-minute limit is the correlation window: a spike more than 5 minutes
after the startup event won't include it in its timeline. If you miss it,
clean up (step 13) and start again - don't change the window.

## 7. Observe the automatic error spike

```bash
curl -s "http://127.0.0.1:8000/events?source=nextrace-runtime&environment=local-validation" | python3 -m json.tool
SPIKE_ID=$(curl -s "http://127.0.0.1:8000/events?source=nextrace-runtime&environment=local-validation" \
  | python3 -c 'import sys,json; print(next(e["id"] for e in json.load(sys.stdin) if e["event_type"]=="error_spike"))')
```

Expect exactly one `error_spike` (`local/validation-run-1:error_spike:<window>`)
with `ai_provider_failure_count: 5` - counts only, no error text.

## 8. Open the investigation

```bash
curl -s "http://127.0.0.1:8000/investigations/$SPIKE_ID" | python3 -m json.tool
```

Expect the timeline `deployment → error_spike`, `temporal_proximity` and
`sequence_relationship` evidence, and the deployment as the ranked
candidate. The score is the existing heuristic, not a probability or proof of
cause.

## 9. Reproducible incident scenarios through the API

`scripts/ingest_scenario.py` posts a realistic event sequence to
`POST /events` - the same authenticated path any producer uses - and prints
the event to investigate. The events are ordinary events labelled
`source=scenario-script` (not seeded demo data, and not presented as a real
integration) in the `local-demo` environment.

```bash
.venv/bin/python scripts/ingest_scenario.py deployment-regression --key-file "$VDIR/ingest.key" \
  --at 2026-10-24T17:50:00Z | tee "$VDIR/deployment-regression.json"
TARGET_ID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["target_event_id"])' "$VDIR/deployment-regression.json")
curl -s "http://127.0.0.1:8000/investigations/$TARGET_ID" | python3 -m json.tool
```

Any fixed `--at` works (omit it for "8 minutes ago"). Running the same
command again returns every event with `"created": false` - nothing is
duplicated.

| Scenario                | Timeline                                                   | Top candidate (score) | Notes                                                                          |
| ----------------------- | ---------------------------------------------------------- | --------------------- | ------------------------------------------------------------------------------ |
| `deployment-regression` | autoscale → deployment → error_spike → rollback → recovery | deployment (100)      | autoscale ranks second (20); the neighbouring service's deployment is excluded |
| `db-latency`            | db_latency → error_spike                                   | db_latency (80)       | no recovery claimed                                                            |
| `config-regression`     | config_change → error_spike → rollback → recovery          | config_change (100)   | the setting's old/new values never reach the AI context                        |

Scores are the existing heuristic (temporal proximity + event-type
relevance + evidence + recovery context), not probabilities. Every reason
and evidence description is observational - "Occurred 1 minute 40 seconds
before the error_spike", "A rollback was followed by recovery after an
error_spike" - never causal.

## 10. Optional: a genuine unhandled-exception spike (isolated database only)

Temporarily renaming a table in **this throwaway database** makes one read
endpoint fail with a real database error inside the real code path. Five
such 500s within 60 seconds produce an `error_spike` with
`unhandled_exception_count: 5`.

The burst must not overlap step 6's provider-failure burst in the error-spike
detector, so it has to start:

- **more than 60 seconds after step 6 finished**, so those failures have left
  the trailing 60-second window. Otherwise the spike fires on the first 500,
  with mixed counts.
- **in a fresh 60-second window** (windows are aligned to whole UTC minutes),
  because at most one spike is recorded per window per process.

The first command below waits until both are true, which takes up to about
two minutes, and needs `FAILURES_DONE` from step 6 in the same shell.

```bash
start=$(( (${FAILURES_DONE:?run step 6 in this shell first} + 61 + 59) / 60 * 60 + 1 ))
while [ "$(date +%s)" -lt "$start" ]; do sleep 1; done     # 1 s into a fresh window
docker exec nextrace-validation-pg psql -U nextrace -d nextrace_validation -c 'ALTER TABLE analysis_runs RENAME TO analysis_runs_fault_injection'
for i in 1 2 3 4 5; do curl -s -o /dev/null -w '%{http_code} ' "http://127.0.0.1:8000/investigations/$SPIKE_ID/analyses"; done; echo   # 500 x5
docker exec nextrace-validation-pg psql -U nextrace -d nextrace_validation -c 'ALTER TABLE analysis_runs_fault_injection RENAME TO analysis_runs'
curl -s "http://127.0.0.1:8000/events?source=nextrace-runtime&environment=local-validation" | python3 -m json.tool
```

Clients only ever see `{"detail": "Internal server error"}`; the log has
the exception server-side and the spike metadata holds counts only.

## 11. Open the investigation in the UI (second terminal)

```bash
cd frontend
npm start -- --host 127.0.0.1 --port 4210     # dev server; proxies the API to port 8000
```

Open `http://127.0.0.1:4210/?event=<TARGET_ID>` (the `?event=` link opens
an investigation directly), or open `http://127.0.0.1:4210/` and pick an
event from **Recent events** (`Hide demo events` filters seeded demo data).
Check:

- **Timeline:** chronological, the target highlighted, and _Investigate this
  event_ on every other row to pivot (for example from the spike to the
  deployment).
- **Candidates:** rank, score, the concrete event (ID, time, message), and
  _Why this candidate?_ with each reason and its supporting evidence.
- **Evidence:** each item's stable `ev1_…` ID, the same IDs the candidates
  cite.
- **AI Investigation:** _Analyze with AI_ asks for the operator key on the
  first 401; paste it from `$VDIR/ingest.key`. The key is held in memory
  only, never stored or put in the URL. With the fault from step 4 still
  active, it shows the controlled "Could not connect to the AI provider"
  message, and _Previous analyses_ lists the failed runs.

## 12. Restore normal configuration (and, optionally, AI analysis)

Restart the **same instance** without the fault variables. The backend then
uses your normal local AI configuration (the repository-root `.env`, loaded
automatically - never echo it). The startup event is deduplicated: the log
shows `event_created: false` for `local/validation-run-1:startup`.

```bash
kill "$(cat "$VDIR/backend.pid")"; while curl -sf http://127.0.0.1:8000/health >/dev/null; do sleep 0.5; done
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > "$VDIR/backend.log" 2>&1 &
echo $! > "$VDIR/backend.pid"
until curl -sf http://127.0.0.1:8000/health >/dev/null; do sleep 0.5; done
until grep -q runtime_event_recorded "$VDIR/backend.log"; do sleep 0.5; done
grep runtime_event_recorded "$VDIR/backend.log"
```

**Optional - only with an approved provider and credentials configured**
(this makes one real, billable provider call):

```bash
curl -s -H @"$VDIR/key.header" -X POST "http://127.0.0.1:8000/investigations/$SPIKE_ID/analysis" | python3 -m json.tool
curl -s "http://127.0.0.1:8000/investigations/$SPIKE_ID/analyses" | python3 -m json.tool
```

Without a configured provider this returns a controlled 503; the
deterministic investigation in step 8 is still the validated result.

With a provider configured, the UI's _Analyze with AI_ (step 11) makes the
same billable call; its result lists the evidence IDs it cites, and any ID
not present in the investigation is removed server-side before display.

## 13. Clean up

Stop the UI dev server (Ctrl+C in its terminal), then:

```bash
kill "$(cat "$VDIR/backend.pid")"
docker stop nextrace-validation-pg      # --rm: the database is deleted
rm -rf "$VDIR"
cd ..
```
