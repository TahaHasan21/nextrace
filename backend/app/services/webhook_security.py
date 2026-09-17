"""Webhook authenticity verification.

Kept independent of FastAPI/Starlette (plain bytes/strings in, bool out) so
it can be unit tested without a request object, and reused unchanged if a
second webhook-based source is added later.

Never logs the secret, the signature header, or the raw request body -
callers must not pass any of those into a log call either.
"""

import hashlib
import hmac

GITHUB_SIGNATURE_PREFIX = "sha256="


def verify_github_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify a GitHub webhook's `X-Hub-Signature-256` header.

    GitHub signs the exact raw request body with HMAC-SHA256, keyed by the
    webhook's configured secret, and sends it as `sha256=<hex digest>`. This
    must be called with the untouched raw bytes Starlette received - never
    bytes re-serialized from parsed JSON, which are not guaranteed to be
    byte-identical to what GitHub actually sent and signed.

    Returns False (never raises) for every invalid input shape: a missing
    secret (server misconfiguration), a missing header, a header without
    the expected "sha256=" prefix, or a header whose digest does not match -
    callers should treat all of these as "not verified," though a missing
    secret specifically is expected to be distinguished and handled by the
    caller *before* this function is even reached (see the webhook router),
    since it represents a server configuration problem, not a client
    authentication failure.
    """
    if not secret:
        return False
    if not signature_header:
        return False
    if not signature_header.startswith(GITHUB_SIGNATURE_PREFIX):
        return False

    provided_digest = signature_header[len(GITHUB_SIGNATURE_PREFIX):]
    expected_digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    # Constant-time comparison - a plain `==` would leak timing information
    # about how many leading characters matched, letting an attacker guess
    # the correct digest one byte at a time.
    return hmac.compare_digest(expected_digest, provided_digest)
