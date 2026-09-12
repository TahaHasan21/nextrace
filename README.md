# Nextrace

> Understand what happened. Discover why.

Nextrace is a deterministic production incident investigation platform. It
reconstructs what happened during an incident from raw events, builds an
evidence-backed timeline, ranks the events most worth investigating, and -
only after all of that - lets an AI explain the result in plain language.

## 1. What Nextrace is

When something breaks in production, the evidence is scattered: a deploy in
one system, a config change in another, a latency graph somewhere else, an
alert in a fourth. Nextrace ingests those events, normalizes them into one
canonical shape, correlates the ones that plausibly relate to each other,
lays them out on a timeline, and generates deterministic evidence about how
they relate - before any AI is involved.

## 2. Why it exists

The actual engineering problem during an incident review is rarely "what
does an LLM think happened" - it's "what do we actually know, and how
confident should we be in it". Nextrace is built around that distinction:
a deterministic evidence layer that is trustworthy on its own, with an
optional AI layer on top that explains the evidence rather than replacing
it.

## 3. Architecture

```text
Event Sources
    ↓
Normalization        (github / application adapters -> CanonicalEvent)
    ↓
Persistence           (PostgreSQL)
    ↓
Correlation            (same service + environment + time window)
    ↓
Timeline                (chronological, target-centered)
    ↓
Evidence                 (temporal_proximity / sequence_relationship / recovery_relationship)
    ↓
Candidate Ranking          (deterministic 0-100 heuristic score)
    ↓
AI Investigation             (optional - explains the evidence above)
```

```text
Angular (frontend)
    ↓
FastAPI (backend)
    ↓
PostgreSQL   +   AI Provider (Anthropic, optional)
```

## 4. Core engineering ideas

These three rules are enforced throughout the codebase, not just stated:

```text
Correlation ≠ Causation
Candidate ranking ≠ root-cause confirmation
AI explanation is grounded in deterministic evidence
```

Concretely:

- The correlation/evidence/candidate engines are pure, deterministic
  Python - no ML, no LLM, no randomness. The same events always produce
  the same timeline, evidence, and scores.
- Candidate scores (0-100) are an explainable heuristic combining temporal
  proximity, event-type relevance, evidence relationships, and recovery
  context - never a probability or a confidence percentage. Ties are
  preserved, never artificially broken.
- The AI layer receives a narrow `InvestigationContext` (target event,
  timeline, evidence, candidates - nothing else) and is instructed to
  never invent events, timestamps, metrics, or evidence, and to say so
  explicitly when the evidence is ambiguous or insufficient. Every
  candidate the AI references is validated against the real candidate
  list server-side before the response is returned; hallucinated
  references are dropped, not trusted.

## 5. Demo scenario

Nextrace ships a deterministic, idempotent payment-service incident used
to exercise and demonstrate the full pipeline:

```text
10:00  deployment       Version 1.4.2 deployed to production
10:01  config_change    Database connection pool configuration changed
10:02  db_latency       Database query latency increased above normal range
10:03  error_spike      HTTP 5xx error rate increased significantly
10:04  incident         Payment failures reported in production
10:07  rollback         Payment service rolled back to version 1.4.1
10:08  recovery         Payment error rate returned to normal range
```

This is a simulated incident, not a claim about what "really" happened in
any real system - it exists to give the investigation pipeline something
concrete and repeatable to reconstruct. Seeding it is idempotent: running
the seed script twice reuses the same seven events (tagged with a
`demo_scenario` metadata marker) rather than creating duplicates.

## 6. AI architecture

```text
                                              ┌─ AnthropicProvider ─→ Anthropic API
Angular → FastAPI → AIProvider abstraction ──┼─ OpenAIProvider ────→ OpenAI API
                                              └─ GeminiProvider ────→ Gemini API
```

The AI is server-side only. The Angular app never holds or sends an API
key - it calls `POST /investigations/{event_id}/analysis` on the FastAPI
backend, and the backend is the only thing that talks to the configured
provider's API. Which concrete provider handles a request is a pure
configuration choice (`AI_PROVIDER=anthropic` / `openai` / `gemini`),
resolved once in `get_ai_provider()` - the investigation engine, the
grounding logic, and the frontend all depend only on the small
`AIProvider` interface (one method: `generate(context) -> InvestigationAnalysis`),
never on any vendor SDK directly. All three providers request structured
output from their SDK's own Pydantic-based mechanism
(`messages.parse(..., output_format=...)` for Anthropic,
`responses.parse(..., text_format=...)` for OpenAI,
`generate_content(..., config=GenerateContentConfig(response_schema=...))`
for Gemini) so the application receives an already-validated
`InvestigationAnalysis`, never hand-parsed JSON embedded in prose.

AI analysis is entirely optional and lazy: it is never called automatically
when an investigation loads. A user clicks "Analyze with AI" to trigger it,
and if no API key is configured, the deterministic investigation still
works fully - only that one endpoint returns a controlled `503`.

## 7. AI limitations

Nextrace does not claim that a candidate's score represents a statistical
probability, and the AI layer does not claim to have determined a root
cause. Concretely:

- A score of 90 does not mean "90% likely to be the cause" - it means the
  event scored 90 out of 100 on a fixed, inspectable set of deterministic
  rules (temporal proximity, event-type relevance, evidence relationships,
  recovery context).
- Two candidates can legitimately tie (the demo scenario intentionally
  demonstrates this: `deployment` and `config_change` both score 90) - the
  system does not force an arbitrary winner.
- The AI is instructed to distinguish observed evidence from its own
  interpretation, to state uncertainty explicitly, and to never claim
  causation that the evidence does not establish. Its candidate and
  evidence references are validated server-side against the real
  candidate/evidence lists before being returned - a hallucinated
  reference is dropped, never passed through. This is structural grounding
  (does the referenced ID exist in the supplied context?), not semantic
  verification of the model's prose.

## 8. Evidence identity

Every evidence item now carries a stable, deterministic ID (e.g.
`ev1_a82f1c9e4b7d0f31`), derived only from its evidence type and its
ordered list of event IDs - never from the human-readable description, its
position in the evidence list, or any other evidence item. The `ev1_`
prefix is a version namespace: if the identity algorithm ever changes, a
new prefix (`ev2_`, ...) would be introduced rather than silently
reassigning existing IDs.

This exists to make references auditable:

- `CandidateRead.supporting_evidence_ids` now genuinely contains evidence
  IDs (`EvidenceRead.id`) - not event IDs, which is what it held before.
  Every ID it lists corresponds to a real item in that same investigation's
  `evidence` list; nothing dangles.
- The AI context layer (`app/services/ai/context.py`) supplies each
  evidence item's ID to the model, so a candidate reference can cite
  exactly which evidence backs it (`CandidateReference.supporting_evidence_ids`).
  Grounding (`ground_analysis`) strips any evidence ID the model cites that
  does not genuinely appear in the supplied investigation context, the
  same way it already stripped hallucinated candidate references.
- The investigation console links a candidate's cited evidence straight to
  the matching entry in the Evidence panel.

**Current limitation:** evidence is still derived at investigation request
time from the timeline - it is not stored in a dedicated evidence table.
The ID is stable and reproducible across requests and process restarts
because it is a pure function of (type, event_ids), but there is no
persistence layer behind it yet. Grounding on evidence references is also
structural only: it verifies a cited evidence ID exists in the supplied
context, not that the model's prose about it is semantically accurate -
that remains a natural next step, not something this batch claims to
solve.

## 9. Running locally

### Prerequisites

- Docker (for PostgreSQL, or for the full Docker Compose stack)
- Python 3.13+
- Node.js 22+ / npm 10+

### Local development (backend + frontend run directly)

```bash
# 1. Database
docker compose up -d postgres

# 2. Backend
cd backend
python -m venv venv
venv\Scripts\activate        # Windows; `source venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
cp .env.example .env         # edit DATABASE_URL if needed
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 3. Frontend (separate terminal)
cd frontend
npm install --legacy-peer-deps
npm start                    # ng serve, proxies API calls to localhost:8000
```

Open `http://localhost:4200`. `GET http://localhost:8000/health` should
return `{"status": "healthy"}`.

### Docker Compose (full stack)

```bash
cp .env.example .env   # optional - see below; works fine with no .env at all
docker compose up --build
```

This builds and starts PostgreSQL, the FastAPI backend (running Alembic
migrations on startup, then `uvicorn`), and the Angular frontend (built and
served as static files by nginx, which also proxies API routes to the
backend container). Open `http://localhost:4200`. AI analysis works exactly
like local development: optional, and gracefully disabled without a key.

**Environment variables (all optional - `docker-compose.yml` has safe
development defaults for every one of them via `${VAR:-default}`, so no
`.env` file is required at all):**

| Variable | Default | Purpose |
|---|---|---|
| `POSTGRES_DB` | `nextrace` | Database name |
| `POSTGRES_USER` | `nextrace` | Database user |
| `POSTGRES_PASSWORD` | `nextrace_dev` | Database password (development only - override via `.env` rather than editing the tracked Compose file) |
| `AI_PROVIDER` | `anthropic` | AI provider selector - `anthropic`, `openai`, or `gemini` |
| `AI_API_KEY` | *(empty)* | API key for the selected provider - leave blank to run with AI analysis disabled |
| `AI_MODEL` | `claude-opus-5` | Model for the selected provider (e.g. `claude-opus-5` for Anthropic, `gpt-6-astra` for OpenAI, `gemini-3.8-flash` for Gemini) |

Copy `.env.example` to `.env` at the repo root only if you want to override
any of these (e.g. to supply a real `AI_API_KEY`, or switch `AI_PROVIDER`
to `openai`). Never commit `.env` - it's gitignored, and only placeholder
values live in `.env.example`.

### Seeding the demo incident

```bash
cd backend
python -c "
from app.db.session import SessionLocal
from app.demo.incident import get_or_create_demo_incident

db = SessionLocal()
try:
    events = get_or_create_demo_incident(db)
    incident_id = next(e.id for e in events if e.event_type == 'incident')
    print('Investigate this event ID:', incident_id)
finally:
    db.close()
"
```

Or, if running via Docker Compose: `docker exec nextrace-backend python -c "..."`
with the same snippet. `get_or_create_demo_incident` is idempotent - running
it again reuses the existing seeded events instead of inserting duplicates.

### Cleaning up old demo duplicates (development only)

Development databases that predate the `demo_scenario` idempotency marker
may have old, unmarked duplicate copies of the demo incident. A small,
explicit, dry-run-by-default script can find and remove exactly those rows
- never arbitrary events, and never automatically:

```bash
cd backend
python scripts/cleanup_demo_data.py           # dry run - reports only
python scripts/cleanup_demo_data.py --apply   # actually deletes
```

See `app/demo/cleanup.py` for the exact (narrow, conservative)
identification rule.

## 10. Testing

### Backend

```bash
cd backend
pytest              # 298 tests
alembic check        # verifies no pending schema drift
```

Coverage includes: event ingestion validation, normalization, persistence,
correlation/timeline/evidence/candidate determinism, evidence identity
(stable/deterministic IDs, order-sensitivity, independence from
description text), the Investigation API, all three AI providers
(Anthropic, OpenAI, Gemini - configuration resolution, structured-output
requests, every mapped SDK failure mode, malformed-response handling, and
that no API key ever appears in a log line or exception message), AI
grounding and safety (candidate-reference validation, evidence-reference
validation, prompt/data-boundary and prompt-injection resistance - all via fake
providers, no live API key required), structured logging/request-ID/
readiness behavior, and demo idempotency/cleanup/concurrency-safety logic.
Tests run against an isolated `nextrace_test` database - the real
development database is never touched by the suite.

### Frontend

```bash
cd frontend
npm test -- --watch=false   # 43 tests
npm run build                 # production build
```

Coverage includes the investigation console, timeline/evidence/candidate
rendering, metadata display, the AI analysis panel (loading/success/error/
unavailable states, primary/alternative candidate evidence citations linked
to the evidence panel, using a fake service - no live API key required),
and loading/error/empty states throughout.

## 11. API examples

```bash
# Ingest an event
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{
    "service": "payment-service",
    "environment": "production",
    "event_type": "deployment",
    "timestamp": "2026-09-07T10:00:00Z",
    "severity": "info",
    "source": "github",
    "message": "Version 1.4.3 deployed to production",
    "metadata": {"version": "1.4.3"}
  }'

# List recent events
curl http://localhost:8000/events

# Get a deterministic investigation (timeline + evidence + ranked candidates)
curl http://localhost:8000/investigations/25

# Ask the (optional) AI layer to explain the investigation
curl -X POST http://localhost:8000/investigations/25/analysis

# Liveness - no database or AI dependency
curl http://localhost:8000/health

# Readiness - verifies PostgreSQL is reachable (AI is NOT required to be "ready")
curl http://localhost:8000/ready
```

Full interactive API docs are available at `http://localhost:8000/docs`
(FastAPI's generated Swagger UI) whenever the backend is running.

## 12. Future roadmap

Realistic next steps, not a wishlist:

- Additional event source adapters (beyond GitHub and generic application
  events) - e.g. Kubernetes events, cloud infrastructure change events.
- Richer deployment metadata capture (diffs, associated PRs/commits).
- Additional evidence relationship types as real incident patterns justify
  them.
- Statistical/anomaly-based signals feeding into candidate scoring,
  alongside (not replacing) the existing deterministic rules.
- Production observability integrations (shipping Nextrace's own
  structured logs somewhere queryable) if Nextrace itself runs in
  production.
- Distributed tracing correlation, if/when Nextrace ingests trace data as
  an event source.

## Portfolio presentation

**Problem.** Production incidents create fragmented evidence across
systems - deploys, config changes, metrics, logs, alerts - with no single
place that reconstructs how they relate.

**Nextrace's approach.** Normalize events → correlate them → construct a
timeline → generate evidence → rank candidates → ask AI to explain the
evidence. Each stage is a small, independently testable module.

**The actual engineering challenge.** Calling an LLM is the easy part. The
hard part is building an evidence model the AI can be trusted to reason
over - one where "the AI said so" is never the only backing for a claim,
where every reference the AI makes can be validated against ground truth,
and where the system can say "I don't know" instead of confabulating an
answer.

**Key design decision.** Deterministic reasoning comes first, and the AI
sits strictly downstream of it. If the AI provider were deleted entirely,
Nextrace would still correctly reconstruct incidents, generate evidence,
and rank candidates - AI only adds an explanation layer on top of a system
that already works without it.

## Project structure

```text
backend/    FastAPI + SQLAlchemy + PostgreSQL + Alembic + AI provider layer
frontend/   Angular 22 investigation console
```
