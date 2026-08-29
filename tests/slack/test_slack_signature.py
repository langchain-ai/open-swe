import hashlib
import hmac
import time

from agent.utils.slack import verify_slack_signature


def _slack_signature(secret: str, timestamp: str, body: bytes) -> str:
    """HMAC over the raw request body, matching Slack's signing scheme."""
    base_string = b"v0:" + timestamp.encode("utf-8") + b":" + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base_string, hashlib.sha256).hexdigest()


def test_verify_slack_signature_accepts_valid_utf8_body() -> None:
    secret = "test-signing-secret"
    timestamp = str(int(time.time()))
    body = b'{"type":"url_verification","challenge":"ok"}'
    signature = _slack_signature(secret, timestamp, body)

    assert verify_slack_signature(body, timestamp, signature, secret) is True


def test_verify_slack_signature_accepts_non_utf8_body() -> None:
    """Slack signs the raw bytes. Invalid UTF-8 must not be lossily re-decoded."""
    secret = "test-signing-secret"
    timestamp = str(int(time.time()))
    body = b"payload=\xff\xfe not utf-8"
    signature = _slack_signature(secret, timestamp, body)

    assert verify_slack_signature(body, timestamp, signature, secret) is True
