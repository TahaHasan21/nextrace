# Demo script (about 10 minutes)

A technical walkthrough of NexTrace on a laptop, with no cloud and no
credentials. Before starting, run steps 1–9 of
[`local-validation.md`](local-validation.md) to bring up the throwaway
database, the backend with runtime telemetry, and the
`deployment-regression` scenario. Then start the UI as in step 11.

## 1. What NexTrace is (1 min)

> When something breaks, the clues are scattered: a deploy in one system,
> an alert in another. NexTrace collects those events, lines up the ones
> that plausibly belong together, and states exactly why each earlier
> event deserves attention. It does not claim to know the cause. AI is an
> optional explanation layer on top, not the engine.

## 2. Trigger a reproducible incident (1 min)

Show the scenario going in through the same authenticated API any producer
uses:

```bash
.venv/bin/python scripts/ingest_scenario.py deployment-regression --key-file "$VDIR/ingest.key"
```

Point out `target_event_id` and that every event says `"created": true`.
Run it again with the same `--at` to show `"created": false`: redelivered
events never duplicate.

Optionally, show NexTrace observing itself (steps 6–7 of the validation
guide): five failing AI calls make the backend record its own
`error_spike`.

## 3. Open the investigation (1 min)

Open `http://127.0.0.1:4210/`, expand **Recent events** and pick the
_Error Spike_ for `checkout-api` (or use `?event=<target_event_id>`). The
header shows the target event, its service and environment.

## 4. Explain the timeline (2 min)

- Correlation is deliberately simple and inspectable: same service, same
  environment, within ±5 minutes.
- The `inventory-api` deployment in the same minutes is **absent**
  (different service), and so is anything older than 5 minutes.
- Events after the spike (rollback, recovery) are context. They are never
  candidates for something that happened before them.
- _Investigate this event_ pivots to any row, for example the recovery,
  where the rollback becomes the leading candidate.

## 5. Inspect the evidence (2 min)

- Each item is an observed relationship: temporal proximity, sequence, or
  recovery.
- The `ev1_…` IDs are stable: a hash of the evidence type and event IDs,
  so they are identical on every request and after restarts.
- Read a description aloud: "Deployment occurred 100 seconds before
  error_spike." Nothing says "caused".

## 6. Candidates and scores (2 min)

- The deployment scores 100 and the autoscale 20. Open _Why this
  candidate?_: every point has a reason (30 for proximity, 25 for a
  deployment, 20 for the sequence, 10 for timing evidence, 15 for recovery
  context) and links to the evidence behind it.
- The score is a ranking heuristic, **not a probability**. Ties are shown
  as ties.

## 7. AI explanation (only if a provider is approved) (1 min)

Click _Analyze with AI_ and enter the operator key, which stays in memory
only. Show that the result separates **observed evidence**, **candidate
interpretation**, **uncertainties** (including what is missing), and
**recommended checks**. Point out that the evidence IDs it cites link back
to the Evidence panel: any ID the model invents is removed server-side.

Without a provider, show the controlled "unavailable" message and the
failed runs under _Previous analyses_. The deterministic investigation is
unaffected.

## 8. Evidence vs. inferred causality (1 min)

> Everything above the AI panel is fact plus a transparent ranking: these
> events happened, in this order, this close together. "The deployment
> caused the errors" is a hypothesis, and the recommended checks (diff the
> release, compare error rates before and after the rollback) are how an
> engineer would confirm or reject it. NexTrace narrows where to look; it
> doesn't pretend to have looked.

Finish with the cleanup step of the validation guide.
