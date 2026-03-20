from __future__ import annotations

import hashlib
import hmac
import time

from agent.utils.github_comments import verify_github_signature
from agent.utils.slack import verify_slack_signature
from agent.webapp import verify_linear_signature

_SECRET = "test-webhook-secret"
_BODY = b'{"event":"test"}'


# -- verify_linear_signature ------------------------------------------------


def test_linear_valid_signature():
    sig = hmac.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    assert verify_linear_signature(_BODY, sig, _SECRET) is True


def test_linear_invalid_signature():
    assert verify_linear_signature(_BODY, "bad-sig", _SECRET) is False


def test_linear_empty_secret():
    sig = hmac.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    assert verify_linear_signature(_BODY, sig, "") is False


def test_linear_wrong_secret():
    sig = hmac.new("wrong-secret".encode(), _BODY, hashlib.sha256).hexdigest()
    assert verify_linear_signature(_BODY, sig, _SECRET) is False


# -- verify_github_signature ------------------------------------------------


def test_github_valid_signature():
    sig = "sha256=" + hmac.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    assert verify_github_signature(_BODY, sig, secret=_SECRET) is True


def test_github_invalid_signature():
    assert verify_github_signature(_BODY, "sha256=bad", secret=_SECRET) is False


def test_github_empty_secret():
    sig = "sha256=" + hmac.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    assert verify_github_signature(_BODY, sig, secret="") is False


def test_github_missing_prefix():
    """A signature without the sha256= prefix should fail."""
    sig = hmac.new(_SECRET.encode(), _BODY, hashlib.sha256).hexdigest()
    assert verify_github_signature(_BODY, sig, secret=_SECRET) is False


# -- verify_slack_signature -------------------------------------------------


def _slack_sign(body: bytes, timestamp: str, secret: str) -> str:
    base = f"v0:{timestamp}:{body.decode('utf-8')}"
    return "v0=" + hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest()


def test_slack_valid_signature():
    ts = str(int(time.time()))
    sig = _slack_sign(_BODY, ts, _SECRET)
    assert verify_slack_signature(_BODY, ts, sig, _SECRET) is True


def test_slack_invalid_signature():
    ts = str(int(time.time()))
    assert verify_slack_signature(_BODY, ts, "v0=bad", _SECRET) is False


def test_slack_empty_secret():
    ts = str(int(time.time()))
    sig = _slack_sign(_BODY, ts, _SECRET)
    assert verify_slack_signature(_BODY, ts, sig, "") is False


def test_slack_empty_timestamp():
    sig = _slack_sign(_BODY, "0", _SECRET)
    assert verify_slack_signature(_BODY, "", sig, _SECRET) is False


def test_slack_expired_timestamp():
    old_ts = str(int(time.time()) - 600)  # 10 minutes ago
    sig = _slack_sign(_BODY, old_ts, _SECRET)
    assert verify_slack_signature(_BODY, old_ts, sig, _SECRET) is False


def test_slack_non_numeric_timestamp():
    assert verify_slack_signature(_BODY, "not-a-number", "v0=abc", _SECRET) is False
