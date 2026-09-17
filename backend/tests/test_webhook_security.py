import hashlib
import hmac

from app.services.webhook_security import verify_github_signature

# Pure-function tests - no db_session/client fixture anywhere in this
# module, matching tests/test_normalization.py's convention.

SECRET = "test-webhook-secret"
BODY = b'{"action": "created", "deployment_status": {"state": "success"}}'


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_is_accepted():
    signature = _sign(BODY, SECRET)
    assert verify_github_signature(BODY, signature, SECRET) is True


def test_incorrect_signature_is_rejected():
    wrong_signature = _sign(BODY, "a-completely-different-secret")
    assert verify_github_signature(BODY, wrong_signature, SECRET) is False


def test_missing_signature_is_rejected():
    assert verify_github_signature(BODY, None, SECRET) is False


def test_empty_signature_header_is_rejected():
    assert verify_github_signature(BODY, "", SECRET) is False


def test_malformed_signature_without_algorithm_prefix_is_rejected():
    digest = hmac.new(SECRET.encode("utf-8"), BODY, hashlib.sha256).hexdigest()
    assert verify_github_signature(BODY, digest, SECRET) is False


def test_malformed_signature_with_wrong_algorithm_prefix_is_rejected():
    digest = hmac.new(SECRET.encode("utf-8"), BODY, hashlib.sha256).hexdigest()
    assert verify_github_signature(BODY, f"sha1={digest}", SECRET) is False


def test_signature_with_non_hex_digest_is_rejected():
    assert verify_github_signature(BODY, "sha256=not-a-real-digest", SECRET) is False


def test_tampered_body_is_rejected():
    signature = _sign(BODY, SECRET)
    tampered_body = BODY.replace(b"success", b"failure")

    assert verify_github_signature(tampered_body, signature, SECRET) is False


def test_missing_secret_is_rejected():
    signature = _sign(BODY, SECRET)
    assert verify_github_signature(BODY, signature, "") is False


def test_missing_secret_and_missing_signature_is_rejected():
    assert verify_github_signature(BODY, None, "") is False


def test_verification_uses_constant_time_comparison(monkeypatch):
    # Asserts compare_digest is actually what decides the outcome, not a
    # plain `==` - patch it to always return True and confirm that flips
    # an otherwise-incorrect signature to "verified".
    monkeypatch.setattr("app.services.webhook_security.hmac.compare_digest", lambda a, b: True)

    wrong_signature = _sign(BODY, "a-completely-different-secret")
    assert verify_github_signature(BODY, wrong_signature, SECRET) is True


def test_empty_body_with_valid_signature_is_accepted():
    empty_body = b""
    signature = _sign(empty_body, SECRET)
    assert verify_github_signature(empty_body, signature, SECRET) is True
