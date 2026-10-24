# Local end-to-end validation with genuine runtime events

Demonstrates the full pipeline on events produced by a **running NexTrace
backend**, with no seeded demo data and nothing inserted by hand:

runtime start → `deployment` event → real application failures → automatic
`error_spike` → correlation → timeline → evidence → ranked candidate →
(optional) AI explanation.

Everything runs locally against a throwaway database. No AWS resources, no
production data, and - except for the optional AI-explanation step - no
credentials or paid API calls.

**Never print the ingest key or any provider key.** The commands below keep
the ingest key in a mode-600 file and pass it to `curl` via a header file.

## Prerequisites

- Docker (any engine; e.g. `export DOCKER_CONTEXT=default` for the native
  engine if Docker Desktop isn't running).
- `backend/.venv` with `backend/requirements.txt` installed.
- Free local ports 55436 (PostgreSQL) and 8000 (backend).

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
```

The 5-minute limit is the correlation window: a spike more than 5 minutes
after the startup event won't include it in its timeline. If you miss it,
clean up (step 10) and start again - don't change the window.

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

## 9. Restore normal configuration (and, optionally, AI analysis)

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

**UI (optional, second terminal):** `cd frontend && npm start` (add
`-- --port 4210` if 4200 is busy), open the printed URL, enter `$SPIKE_ID`,
and use _Analyze with AI_. It asks for the operator key: paste it from
`$VDIR/ingest.key`. The key is held in memory only and never stored.

## 10. Clean up

```bash
kill "$(cat "$VDIR/backend.pid")"
docker stop nextrace-validation-pg      # --rm: the database is deleted
rm -rf "$VDIR"
cd ..
```
