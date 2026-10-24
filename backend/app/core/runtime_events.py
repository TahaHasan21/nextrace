"""Runtime telemetry: genuine operational events emitted by NexTrace itself.

When explicitly enabled, the running backend records two kinds of events
about itself through the same CanonicalEvent -> ingest_event() path every
other source uses (no new table, migration, AWS service, or dependency):

1. `deployment` - once per ECS task, when the task's application starts.
2. `error_spike` - when at least `threshold` eligible failures (default 5)
   occur within `window_seconds` (default 60) on this task.

Both use source="nextrace-runtime" and the service/environment configured
below, so they correlate with each other (same service + environment +
time window) exactly like any other source's events - a deployment that
precedes an error spike becomes an evidence-backed *candidate* explanation
for it, never a proven cause.

Task identity and deduplication
-------------------------------
The task identity is the real ECS task ID, read from the ECS task metadata
endpoint v4 (ECS_CONTAINER_METADATA_URI_V4, injected automatically into
every Fargate task). No identifier is ever fabricated: if the endpoint is
absent (local Docker, tests) or unreadable, no runtime events are recorded
at all and the reason is logged once.

source_event_id values (deduplicated by the existing (source,
source_event_id) unique constraint):

    task/<task-id>:startup
        One per task. A process restart inside the same task (or several
        workers in one task) re-submits the same id and gets the existing
        row back - never a duplicate.

    task/<task-id>:error_spike:<bucket-start, e.g. 20261020T101500Z>
        Buckets are fixed windows of `window_seconds` aligned to the Unix
        epoch. At most one error_spike per task per bucket is emitted (an
        in-memory guard, backed by the database constraint). A sustained
        failure burst therefore yields one event per window, not one per
        failure.

Safety properties
-----------------
- Disabled unless NEXTRACE_RUNTIME_TELEMETRY_ENABLED is true AND a
  service and environment are configured.
- Never blocks startup: identity lookup and the startup write run on a
  background thread.
- No database write on the request path: requests only update an
  in-memory counter; the (rare) error_spike write is dispatched to a
  background thread with its own database session.
- Telemetry failures never change an API response, never raise to a
  caller, and are never counted as application failures - so a failing
  telemetry write cannot cause further error spikes.
- Events contain counts, failure categories, and task/image identity only -
  never exception messages, stack traces, request data, headers, or
  credentials.
"""

import os
import re
import threading
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.logging import get_logger
from app.normalization.models import CanonicalEvent
from app.services.ingestion import ingest_event

SOURCE_NAME = "nextrace-runtime"

ENABLED_ENV_VAR = "NEXTRACE_RUNTIME_TELEMETRY_ENABLED"
SERVICE_ENV_VAR = "NEXTRACE_RUNTIME_SERVICE"
ENVIRONMENT_ENV_VAR = "NEXTRACE_RUNTIME_ENVIRONMENT"
THRESHOLD_ENV_VAR = "NEXTRACE_ERROR_SPIKE_THRESHOLD"
WINDOW_ENV_VAR = "NEXTRACE_ERROR_SPIKE_WINDOW_SECONDS"
ECS_METADATA_ENV_VAR = "ECS_CONTAINER_METADATA_URI_V4"

DEFAULT_ERROR_SPIKE_THRESHOLD = 5
DEFAULT_ERROR_SPIKE_WINDOW_SECONDS = 60
METADATA_REQUEST_TIMEOUT_SECONDS = 2.0

# Eligible failure categories - the only things the error-spike detector
# counts. Deliberately excluded: 4xx client errors, configuration 503s
# (e.g. no ingest key / webhook secret / AI key configured), readiness
# probes, and telemetry's own failures.
FAILURE_UNHANDLED_EXCEPTION = "unhandled_exception"
FAILURE_AI_PROVIDER = "ai_provider_failure"
FAILURE_KINDS = (FAILURE_UNHANDLED_EXCEPTION, FAILURE_AI_PROVIDER)

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
# ECS task IDs are 32 hex characters today; accept a slightly wider, still
# strict, charset rather than anything an unexpected payload might contain.
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,128}$")
_SERVICE_MAX_LENGTH = 100
_ENVIRONMENT_MAX_LENGTH = 50

logger = get_logger("runtime_telemetry")


# --- Configuration -------------------------------------------------------


@dataclass(frozen=True)
class TelemetryConfig:
    service: str
    environment: str
    error_spike_threshold: int = DEFAULT_ERROR_SPIKE_THRESHOLD
    error_spike_window_seconds: int = DEFAULT_ERROR_SPIKE_WINDOW_SECONDS


def _positive_int(raw: str | None, default: int, name: str) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        value = 0
    if value < 1:
        logger.warning(
            "invalid runtime telemetry setting; using default",
            extra={"event": "runtime_telemetry_invalid_setting", "reason": name},
        )
        return default
    return value


def load_config(environ: Mapping[str, str]) -> TelemetryConfig | None:
    """Telemetry configuration, or None when telemetry must stay off."""
    if (environ.get(ENABLED_ENV_VAR) or "").strip().lower() not in _TRUE_VALUES:
        return None

    service = (environ.get(SERVICE_ENV_VAR) or "").strip()
    environment = (environ.get(ENVIRONMENT_ENV_VAR) or "").strip()
    if (
        not service
        or not environment
        or len(service) > _SERVICE_MAX_LENGTH
        or len(environment) > _ENVIRONMENT_MAX_LENGTH
    ):
        logger.warning(
            "runtime telemetry enabled but service/environment not configured; disabled",
            extra={"event": "runtime_telemetry_misconfigured"},
        )
        return None

    return TelemetryConfig(
        service=service,
        environment=environment,
        error_spike_threshold=_positive_int(
            environ.get(THRESHOLD_ENV_VAR), DEFAULT_ERROR_SPIKE_THRESHOLD, THRESHOLD_ENV_VAR
        ),
        error_spike_window_seconds=_positive_int(
            environ.get(WINDOW_ENV_VAR), DEFAULT_ERROR_SPIKE_WINDOW_SECONDS, WINDOW_ENV_VAR
        ),
    )


# --- Task identity -------------------------------------------------------


@dataclass(frozen=True)
class TaskIdentity:
    task_id: str
    task_definition_family: str | None = None
    task_definition_revision: str | None = None
    availability_zone: str | None = None
    # Repository and tag only (e.g. "nextrace-backend:e066004") - the
    # registry host, which embeds the AWS account ID, is stripped.
    image: str | None = None
    image_digest: str | None = None

    def as_metadata(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "task_id": self.task_id,
                "task_definition_family": self.task_definition_family,
                "task_definition_revision": self.task_definition_revision,
                "availability_zone": self.availability_zone,
                "image": self.image,
                "image_digest": self.image_digest,
            }.items()
            if value is not None
        }


def _http_get_json(url: str) -> Any:
    response = httpx.get(url, timeout=METADATA_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _optional_str(value: Any) -> str | None:
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
        return str(value).strip()
    return None


def _image_without_registry(image: Any) -> str | None:
    image = _optional_str(image)
    if image is None:
        return None
    return image.rsplit("/", 1)[-1]


def fetch_task_identity(
    environ: Mapping[str, str],
    http_get: Callable[[str], Any] = _http_get_json,
) -> TaskIdentity | None:
    """Read this task's identity from the ECS task metadata endpoint v4.

    Returns None - never a made-up identity - when the endpoint is not
    configured, unreachable, or returns no usable TaskARN. Container
    details (image) are best-effort: their absence does not invalidate the
    task identity.
    """
    base_url = (environ.get(ECS_METADATA_ENV_VAR) or "").strip()
    if not base_url:
        return None

    try:
        task = http_get(f"{base_url}/task")
    except Exception as exc:
        logger.warning(
            "ECS task metadata unavailable",
            extra={
                "event": "runtime_telemetry_metadata_unavailable",
                "error_type": type(exc).__name__,
            },
        )
        return None

    task_arn = task.get("TaskARN") if isinstance(task, dict) else None
    if not isinstance(task_arn, str) or "/" not in task_arn:
        return None
    task_id = task_arn.rsplit("/", 1)[-1]
    if not _TASK_ID_PATTERN.match(task_id):
        return None

    image = image_digest = None
    try:
        container = http_get(base_url)
        if isinstance(container, dict):
            image = _image_without_registry(container.get("Image"))
            digest = _optional_str(container.get("ImageID"))
            image_digest = digest if digest and digest.startswith("sha256:") else None
    except Exception as exc:
        logger.info(
            "ECS container metadata unavailable; continuing without image identity",
            extra={
                "event": "runtime_telemetry_container_metadata_unavailable",
                "error_type": type(exc).__name__,
            },
        )

    return TaskIdentity(
        task_id=task_id,
        task_definition_family=_optional_str(task.get("Family")),
        task_definition_revision=_optional_str(task.get("Revision")),
        availability_zone=_optional_str(task.get("AvailabilityZone")),
        image=image,
        image_digest=image_digest,
    )


# --- Error-spike detection ----------------------------------------------


@dataclass(frozen=True)
class SpikeObservation:
    observed_at: datetime
    bucket_start: datetime
    failure_count: int
    counts_by_kind: dict[str, int]


class ErrorSpikeDetector:
    """In-memory sliding-window failure counter. Thread-safe; no I/O.

    `record()` returns a SpikeObservation the first time, within a given
    epoch-aligned bucket, that the number of failures inside the trailing
    window reaches the threshold - and None otherwise.
    """

    def __init__(self, threshold: int, window_seconds: int) -> None:
        self.threshold = threshold
        self.window_seconds = window_seconds
        self._window = timedelta(seconds=window_seconds)
        self._failures: deque[tuple[datetime, str]] = deque()
        self._last_emitted_bucket: datetime | None = None
        self._lock = threading.Lock()

    def bucket_start(self, moment: datetime) -> datetime:
        epoch_seconds = int(moment.timestamp())
        start = epoch_seconds - (epoch_seconds % self.window_seconds)
        return datetime.fromtimestamp(start, tz=timezone.utc)

    def failures_in_window(self) -> int:
        with self._lock:
            return len(self._failures)

    def record(self, kind: str, now: datetime) -> SpikeObservation | None:
        with self._lock:
            self._failures.append((now, kind))
            cutoff = now - self._window
            while self._failures and self._failures[0][0] <= cutoff:
                self._failures.popleft()

            if len(self._failures) < self.threshold:
                return None

            bucket = self.bucket_start(now)
            if bucket == self._last_emitted_bucket:
                return None
            self._last_emitted_bucket = bucket

            counts = {failure_kind: 0 for failure_kind in FAILURE_KINDS}
            for _, failure_kind in self._failures:
                counts[failure_kind] = counts.get(failure_kind, 0) + 1

            return SpikeObservation(
                observed_at=now,
                bucket_start=bucket,
                failure_count=len(self._failures),
                counts_by_kind=counts,
            )


# --- Telemetry recorder --------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _run_in_background_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="nextrace-runtime-telemetry", daemon=True).start()


class RuntimeTelemetry:
    def __init__(
        self,
        config: TelemetryConfig,
        *,
        session_factory: Callable[[], Any],
        identity_fetcher: Callable[[], TaskIdentity | None],
        dispatch: Callable[[Callable[[], None]], None] = _run_in_background_thread,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.config = config
        self._session_factory = session_factory
        self._identity_fetcher = identity_fetcher
        self._dispatch = dispatch
        self._now = now
        self._started_at = now()
        self._identity: TaskIdentity | None = None
        self.detector = ErrorSpikeDetector(
            config.error_spike_threshold, config.error_spike_window_seconds
        )

    @property
    def identity(self) -> TaskIdentity | None:
        return self._identity

    def start(self) -> None:
        """Resolve the task identity and record the startup event - off the
        caller's thread (in production), so startup never waits on it."""
        self._dispatch(self._bootstrap)

    def _bootstrap(self) -> None:
        try:
            identity = self._identity_fetcher()
        except Exception as exc:
            identity = None
            logger.warning(
                "runtime telemetry identity lookup failed",
                extra={
                    "event": "runtime_telemetry_identity_failed",
                    "error_type": type(exc).__name__,
                },
            )

        if identity is None:
            logger.warning(
                "runtime telemetry: no ECS task identity; runtime events will not be recorded",
                extra={"event": "runtime_telemetry_skipped", "reason": "task_identity_unavailable"},
            )
            return

        self._identity = identity
        self._write(self.build_startup_event(identity))

    def build_startup_event(self, identity: TaskIdentity) -> CanonicalEvent:
        details = []
        if identity.task_definition_family and identity.task_definition_revision:
            details.append(
                f"task definition {identity.task_definition_family}:"
                f"{identity.task_definition_revision}"
            )
        if identity.image:
            details.append(f"image {identity.image}")
        suffix = f" ({', '.join(details)})" if details else ""

        return CanonicalEvent(
            service=self.config.service,
            environment=self.config.environment,
            event_type="deployment",
            timestamp=self._started_at,
            severity="info",
            source=SOURCE_NAME,
            source_event_id=f"task/{identity.task_id}:startup",
            message=f"{self.config.service} task started{suffix}",
            metadata=identity.as_metadata(),
        )

    def build_error_spike_event(
        self, identity: TaskIdentity, observation: SpikeObservation
    ) -> CanonicalEvent:
        bucket_label = observation.bucket_start.strftime("%Y%m%dT%H%M%SZ")
        window = self.config.error_spike_window_seconds
        metadata = {
            "task_id": identity.task_id,
            "failure_count": observation.failure_count,
            "unhandled_exception_count": observation.counts_by_kind.get(
                FAILURE_UNHANDLED_EXCEPTION, 0
            ),
            "ai_provider_failure_count": observation.counts_by_kind.get(FAILURE_AI_PROVIDER, 0),
            "threshold": self.config.error_spike_threshold,
            "window_seconds": window,
            "window_start": observation.bucket_start.isoformat(),
        }
        if identity.task_definition_revision is not None:
            metadata["task_definition_revision"] = identity.task_definition_revision

        return CanonicalEvent(
            service=self.config.service,
            environment=self.config.environment,
            event_type="error_spike",
            timestamp=observation.observed_at,
            severity="critical",
            source=SOURCE_NAME,
            source_event_id=f"task/{identity.task_id}:error_spike:{bucket_label}",
            message=(
                f"{observation.failure_count} application failures within {window} seconds "
                f"on {self.config.service}"
            ),
            metadata=metadata,
        )

    def record_failure(self, kind: str) -> None:
        """Count one eligible application failure. Never raises; never
        writes to the database on the caller's thread."""
        try:
            observation = self.detector.record(kind, self._now())
            if observation is None:
                return

            identity = self._identity
            if identity is None:
                logger.warning(
                    "error spike detected but no task identity; not recorded",
                    extra={
                        "event": "runtime_telemetry_skipped",
                        "reason": "task_identity_unavailable",
                    },
                )
                return

            event = self.build_error_spike_event(identity, observation)
            self._dispatch(lambda: self._write(event))
        except Exception as exc:
            logger.warning(
                "runtime telemetry failure recording failed",
                extra={"event": "runtime_telemetry_error", "error_type": type(exc).__name__},
            )

    def _write(self, canonical: CanonicalEvent) -> None:
        """Persist via the shared idempotent ingestion path, in a dedicated
        session. Any failure is logged (type only - never the message,
        which could echo connection details) and swallowed; it is never
        reported back into the failure detector."""
        try:
            session = self._session_factory()
            try:
                event, created = ingest_event(session, canonical)
            finally:
                session.close()
        except Exception as exc:
            logger.warning(
                "runtime telemetry write failed",
                extra={
                    "event": "runtime_telemetry_write_failed",
                    "source_event_id": canonical.source_event_id,
                    "error_type": type(exc).__name__,
                },
            )
            return

        logger.info(
            "runtime event recorded",
            extra={
                "event": "runtime_event_recorded",
                "event_id": event.id,
                "event_created": created,
                "source": SOURCE_NAME,
                "source_event_id": canonical.source_event_id,
            },
        )


# --- Process-wide instance ----------------------------------------------

_telemetry: RuntimeTelemetry | None = None


def _default_session_factory() -> Callable[[], Any]:
    # Imported lazily: app.db.session requires DATABASE_URL at import time.
    from app.db.session import SessionLocal

    return SessionLocal


def start_runtime_telemetry(
    *,
    environ: Mapping[str, str] | None = None,
    session_factory: Callable[[], Any] | None = None,
    http_get: Callable[[str], Any] | None = None,
    dispatch: Callable[[Callable[[], None]], None] | None = None,
    now: Callable[[], datetime] | None = None,
) -> RuntimeTelemetry | None:
    """Configure process-wide runtime telemetry from the environment.

    Called once from the application lifespan. Never raises: any problem
    leaves telemetry disabled and the application starting normally.
    """
    global _telemetry
    try:
        env = os.environ if environ is None else environ
        config = load_config(env)
        if config is None:
            _telemetry = None
            logger.info("runtime telemetry disabled", extra={"event": "runtime_telemetry_disabled"})
            return None

        fetch = http_get or _http_get_json
        telemetry = RuntimeTelemetry(
            config,
            session_factory=session_factory or _default_session_factory(),
            identity_fetcher=lambda: fetch_task_identity(env, fetch),
            dispatch=dispatch or _run_in_background_thread,
            now=now or _utc_now,
        )
        _telemetry = telemetry
        logger.info(
            "runtime telemetry enabled",
            extra={"event": "runtime_telemetry_enabled", "environment": config.environment},
        )
        telemetry.start()
        return telemetry
    except Exception as exc:
        _telemetry = None
        logger.warning(
            "runtime telemetry could not start; continuing without it",
            extra={"event": "runtime_telemetry_error", "error_type": type(exc).__name__},
        )
        return None


def stop_runtime_telemetry() -> None:
    global _telemetry
    _telemetry = None


def get_runtime_telemetry() -> RuntimeTelemetry | None:
    return _telemetry


def record_application_failure(kind: str) -> None:
    """Hook for the API's failure paths. A no-op when telemetry is off;
    never raises either way."""
    telemetry = _telemetry
    if telemetry is None:
        return
    try:
        telemetry.record_failure(kind)
    except Exception:
        pass
