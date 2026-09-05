"""Guided ``.env`` setup for the GitHub + Slack minimum installation.

Run with ``make setup`` (or ``uv run python scripts/setup_env.py``). Prompts for
the handful of credentials Open SWE cannot discover on its own, generates the
local secrets, and writes ``.env`` with owner-only permissions. Existing values
are kept unless ``--force`` is given; secrets are never echoed back.
"""

import argparse
import getpass
import os
import re
import secrets
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from cryptography.fernet import Fernet

MINIMUM_KEYS: tuple[str, ...] = (
    "LANGSMITH_API_KEY",
    "GITHUB_APP_CLIENT_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_WEBHOOK_SECRET",
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
)
MODEL_PROVIDER_KEYS: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gateway": None,
}
DASHBOARD_KEYS: tuple[str, ...] = (
    "GITHUB_APP_CLIENT_SECRET",
    "DASHBOARD_BASE_URL",
    "DASHBOARD_API_BASE_URL",
    "CONFIGURED_ADMINS",
)
GENERATED_KEYS: tuple[str, ...] = ("DASHBOARD_JWT_SECRET", "TOKEN_ENCRYPTION_KEY")
SECRET_KEYS: frozenset[str] = frozenset(
    {
        "LANGSMITH_API_KEY",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_WEBHOOK_SECRET",
        "GITHUB_APP_CLIENT_SECRET",
        "SLACK_BOT_TOKEN",
        "SLACK_SIGNING_SECRET",
        *GENERATED_KEYS,
        *(key for key in MODEL_PROVIDER_KEYS.values() if key),
    }
)
DEFAULT_DASHBOARD_BASE_URL = "http://localhost:3000"
DEFAULT_DASHBOARD_API_BASE_URL = "http://localhost:2024"
MARKER = "# Added by scripts/setup_env.py"

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

Ask = Callable[[str, str], str]
AskSecret = Callable[[str], str]


class SetupError(Exception):
    """A configuration problem the user has to fix; reported without a traceback."""


def generate_dashboard_jwt_secret() -> str:
    """HMAC secret for dashboard session cookies and OAuth state (64 hex chars)."""
    return secrets.token_hex(32)


def generate_token_encryption_key() -> str:
    """Fernet key for stored OAuth tokens; drawn independently of the JWT secret."""
    return Fernet.generate_key().decode()


def generate_webhook_secret() -> str:
    return secrets.token_hex(32)


_ESCAPES = {"n": "\n", "\\": "\\", '"': '"'}


def _decode_double_quoted(inner: str) -> str:
    """Undo :func:`quote_value`; mirrors python-dotenv's double-quote escapes."""
    out: list[str] = []
    chars = iter(inner)
    for char in chars:
        if char != "\\":
            out.append(char)
            continue
        nxt = next(chars, "")
        out.append(_ESCAPES.get(nxt, "\\" + nxt))
    return "".join(out)


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        inner = value[1:-1]
        return _decode_double_quoted(inner) if value[0] == '"' else inner
    return value.split(" #", 1)[0].strip()


def parse_env(text: str) -> dict[str, str]:
    """``KEY=value`` pairs from a dotenv file; comments and blank lines are skipped."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if match:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def quote_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def render_env(existing_text: str, updates: Mapping[str, str]) -> str:
    """Return ``existing_text`` with ``updates`` applied in place and new keys appended.

    Comments, ordering and unrelated keys are preserved; a key present in the
    file is rewritten on its own line and never duplicated.
    """
    pending = dict(updates)
    lines: list[str] = []
    for line in existing_text.splitlines():
        match = _ENV_LINE.match(line)
        key = match.group(1) if match and not line.lstrip().startswith("#") else None
        if key is not None and key in pending:
            lines.append(f"{key}={quote_value(pending.pop(key))}")
        else:
            lines.append(line)
    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        if MARKER not in lines:
            lines.append(MARKER)
        lines.extend(f"{key}={quote_value(value)}" for key, value in pending.items())
    return "\n".join(lines).rstrip("\n") + "\n"


def read_private_key(path: str) -> str:
    """Load a GitHub App ``.pem`` as a single dotenv-safe line."""
    pem = Path(path).expanduser()
    try:
        text = pem.read_text().strip()
    except OSError as exc:
        raise SetupError(f"Cannot read private key file {pem}: {exc.strerror}") from exc
    if "PRIVATE KEY" not in text:
        raise SetupError(f"{pem} does not look like a PEM private key")
    return "\\n".join(line.strip() for line in text.splitlines())


def write_env_file(path: Path, content: str) -> None:
    """Write the file readable by the owner only, creating or truncating it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(content)
    os.chmod(path, 0o600)


def missing_minimum(values: Mapping[str, str]) -> list[str]:
    missing = [key for key in MINIMUM_KEYS if not values.get(key, "").strip()]
    gateway = values.get("LANGSMITH_GATEWAY_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    has_model_key = any(values.get(key, "").strip() for key in MODEL_PROVIDER_KEYS.values() if key)
    if not gateway and not has_model_key:
        missing.append("a model provider key (or LANGSMITH_GATEWAY_ENABLED=true)")
    return missing


def collect_answers(
    ask: Ask,
    ask_secret: AskSecret,
    *,
    existing: Mapping[str, str],
    dashboard: bool,
) -> dict[str, str]:
    """Prompt for every minimum value not already present in ``existing``."""
    answers: dict[str, str] = {}

    def want(key: str) -> bool:
        return not existing.get(key, "").strip()

    if want("LANGSMITH_API_KEY"):
        answers["LANGSMITH_API_KEY"] = ask_secret("LangSmith API key (lsv2_...)")

    has_model = any(existing.get(key, "").strip() for key in MODEL_PROVIDER_KEYS.values() if key)
    gateway_on = existing.get("LANGSMITH_GATEWAY_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not has_model and not gateway_on:
        provider = ask("Model provider (anthropic / openai / google / gateway)", "anthropic")
        provider = provider.strip().lower()
        if provider not in MODEL_PROVIDER_KEYS:
            raise SetupError(f"Unknown model provider {provider!r}")
        key = MODEL_PROVIDER_KEYS[provider]
        if key is None:
            answers["LANGSMITH_GATEWAY_ENABLED"] = "true"
        else:
            answers[key] = ask_secret(f"{provider.title()} API key")

    if want("GITHUB_APP_CLIENT_ID"):
        answers["GITHUB_APP_CLIENT_ID"] = ask("GitHub App client ID (Iv1....)", "").strip()
    if want("GITHUB_APP_PRIVATE_KEY"):
        pem_path = ask("Path to the GitHub App private key (.pem)", "").strip()
        answers["GITHUB_APP_PRIVATE_KEY"] = read_private_key(pem_path)
    if want("GITHUB_WEBHOOK_SECRET"):
        supplied = ask_secret("GitHub webhook secret (leave empty to generate one)")
        answers["GITHUB_WEBHOOK_SECRET"] = supplied.strip() or generate_webhook_secret()
        answers["_generated_webhook_secret"] = "" if supplied.strip() else "1"
    if want("SLACK_BOT_TOKEN"):
        answers["SLACK_BOT_TOKEN"] = ask_secret("Slack bot token (xoxb-...)")
    if want("SLACK_SIGNING_SECRET"):
        answers["SLACK_SIGNING_SECRET"] = ask_secret("Slack signing secret")

    if dashboard:
        if want("GITHUB_APP_CLIENT_SECRET"):
            answers["GITHUB_APP_CLIENT_SECRET"] = ask_secret("GitHub App client secret")
        if want("DASHBOARD_BASE_URL"):
            answers["DASHBOARD_BASE_URL"] = ask("Dashboard URL", DEFAULT_DASHBOARD_BASE_URL)
        if want("DASHBOARD_API_BASE_URL"):
            answers["DASHBOARD_API_BASE_URL"] = ask(
                "Backend URL browsers use", DEFAULT_DASHBOARD_API_BASE_URL
            )
        if want("CONFIGURED_ADMINS"):
            answers["CONFIGURED_ADMINS"] = ask("Admin GitHub logins (comma-separated)", "")

    return {key: value for key, value in answers.items() if value or key.startswith("_")}


def generated_secrets(existing: Mapping[str, str], *, force: bool) -> dict[str, str]:
    """Freshly generated local secrets for every key that is unset (or all, with ``force``)."""
    generators = {
        "DASHBOARD_JWT_SECRET": generate_dashboard_jwt_secret,
        "TOKEN_ENCRYPTION_KEY": generate_token_encryption_key,
    }
    return {
        key: make()
        for key, make in generators.items()
        if force or not existing.get(key, "").strip()
    }


def _values_from_environ(environ: Mapping[str, str], keys: Iterable[str]) -> dict[str, str]:
    return {key: environ[key] for key in keys if environ.get(key, "").strip()}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Guided .env setup for Open SWE")
    parser.add_argument("--output", default=".env", help="dotenv file to write (default: .env)")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="read values from the process environment instead of prompting",
    )
    parser.add_argument(
        "--force", action="store_true", help="regenerate local secrets even if already set"
    )
    parser.add_argument(
        "--dashboard",
        action="store_true",
        help="also collect the optional dashboard settings",
    )
    return parser.parse_args(argv)


def _interactive_ask(prompt: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ")
    return answer if answer.strip() else default


def _interactive_ask_secret(prompt: str) -> str:
    return getpass.getpass(f"{prompt}: ")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output = Path(args.output)
    existing_text = output.read_text() if output.exists() else ""
    existing = parse_env(existing_text)

    if args.non_interactive:
        keys = (*MINIMUM_KEYS, *DASHBOARD_KEYS, "LANGSMITH_GATEWAY_ENABLED")
        keys = (*keys, *(key for key in MODEL_PROVIDER_KEYS.values() if key))
        answers = _values_from_environ(os.environ, keys)
        merged = {**existing, **answers}
        if not merged.get("GITHUB_WEBHOOK_SECRET", "").strip():
            answers["GITHUB_WEBHOOK_SECRET"] = generate_webhook_secret()
            answers["_generated_webhook_secret"] = "1"
    else:
        try:
            answers = collect_answers(
                _interactive_ask,
                _interactive_ask_secret,
                existing=existing,
                dashboard=args.dashboard,
            )
        except (SetupError, EOFError, KeyboardInterrupt) as exc:
            print(f"\nSetup aborted: {exc}" if str(exc) else "\nSetup aborted.", file=sys.stderr)
            return 2

    generated_webhook = bool(answers.pop("_generated_webhook_secret", ""))
    answers.update(generated_secrets(existing, force=args.force))
    merged = {**existing, **answers}
    missing = missing_minimum(merged)
    if missing:
        print("Still missing: " + ", ".join(missing), file=sys.stderr)
        if args.non_interactive:
            return 2

    write_env_file(output, render_env(existing_text, answers))

    written = sorted(answers)
    kept = sorted(key for key in existing if key not in answers)
    print(f"Wrote {output} (mode 600).")
    if written:
        print("  set:  " + ", ".join(written))
    if kept:
        print("  kept: " + ", ".join(kept))
    if generated_webhook:
        print(
            f"\nGITHUB_WEBHOOK_SECRET was generated and saved to {output}. Copy it into the "
            "GitHub App's Webhook secret field; the value is not shown here:\n"
            f"  grep '^GITHUB_WEBHOOK_SECRET=' {output}"
        )
    print("\nNext: `make dev`, then mention the bot in Slack or a GitHub issue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
