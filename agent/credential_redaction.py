"""Redact user credentials while retaining private per-thread bindings."""

import re

CredentialBindings = dict[str, str]

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    ("langsmith_pat", re.compile(r"lsv2_pt_[A-Za-z0-9_-]+")),
    ("langsmith_secret", re.compile(r"lsv2_sk_[A-Za-z0-9_-]+")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9_]+")),
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_-]+")),
    ("slack_token", re.compile(r"xox[bpas]-[A-Za-z0-9-]+")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
)

_BINDINGS: dict[str, CredentialBindings] = {}


def redact_credentials(text: str, binding_key: str | None = None) -> tuple[str, CredentialBindings]:
    """Replace credential-shaped text with opaque references."""
    bindings: CredentialBindings = {}
    redacted = text
    offset = len(_BINDINGS.get(binding_key or "", {}))
    for kind, pattern in _PATTERNS:

        def replace(match: re.Match[str], *, credential_kind: str = kind) -> str:
            reference = f'<redacted-credential id="{offset + len(bindings) + 1}" kind="{credential_kind}"/>'
            bindings[reference] = match.group(0)
            return reference

        redacted = pattern.sub(replace, redacted)
    if binding_key and bindings:
        _BINDINGS.setdefault(binding_key, {}).update(bindings)
    return redacted, bindings


def resolve_credentials[T](value: T, binding_key: str | None) -> T:
    """Resolve opaque credential references only at an execution boundary."""
    bindings = _BINDINGS.get(binding_key or "", {})
    if not bindings:
        return value
    if isinstance(value, str):
        for reference, secret in bindings.items():
            value = value.replace(reference, secret)
        return value  # type: ignore[return-value]
    if isinstance(value, dict):
        return {key: resolve_credentials(item, binding_key) for key, item in value.items()}  # type: ignore[return-value]
    if isinstance(value, list):
        return [resolve_credentials(item, binding_key) for item in value]  # type: ignore[return-value]
    return value
