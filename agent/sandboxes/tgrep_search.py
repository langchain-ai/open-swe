"""Persistent indexed search for sandbox repositories."""

import json
import logging
import shlex

from deepagents.backends.protocol import GrepMatch, GrepResult, SandboxBackendProtocol

logger = logging.getLogger(__name__)
_START = "__OPEN_SWE_TGREP_START__"
_END = "__OPEN_SWE_TGREP_END__"


def build_tgrep_server_command(repo_path: str) -> str:
    """Build an idempotent command that warms one snapshot-prebuilt index."""
    path = shlex.quote(repo_path)
    return (
        f"root=$(git -C {path} rev-parse --show-toplevel 2>/dev/null) || exit 0; "
        'root=$(realpath "$root") || exit 0; '
        r'key=$(printf %s "$root" | sha256sum | cut -d\  -f1); '
        'index="/opt/open-swe/tgrep-indexes/$key"; '
        'test -x /usr/local/bin/tgrep -a -f "$index/lookup.bin" || exit 0; '
        'if /usr/local/bin/tgrep status "$root" --index-path "$index" 2>/dev/null '
        "| grep -q '^Server status for'; then exit 0; fi; "
        'rm -f "$index/serve.json"; '
        'nohup /usr/local/bin/tgrep serve "$root" --index-path "$index" '
        ">>/tmp/open-swe-tgrep.log 2>&1 </dev/null & "
        "for attempt in $(seq 1 100); do "
        '/usr/local/bin/tgrep status "$root" --index-path "$index" 2>/dev/null '
        "| grep -q '^Server status for' && exit 0; sleep .05; done; exit 1"
    )


async def warm_tgrep_server(
    backend: SandboxBackendProtocol,
    repo_path: str,
) -> None:
    """Best-effort warm a repository's persistent search server."""
    try:
        result = await backend.aexecute(build_tgrep_server_command(repo_path), timeout=15)
        if result.exit_code != 0:
            logger.warning("Failed to warm indexed sandbox search")
    except Exception:  # noqa: BLE001
        logger.warning("Failed to warm indexed sandbox search", exc_info=True)


def build_tgrep_command(
    pattern: str,
    path: str | None,
    glob: str | None,
    max_count: int | None = None,
) -> str:
    """Build a direct server-backed search command with shell-quoted inputs."""
    search_path = path or "."
    probe = shlex.quote(search_path if search_path.endswith("/") else f"{search_path}/.")
    command = [
        "/usr/local/bin/tgrep",
        "--json",
        "--fixed-strings",
        "--color",
        "never",
    ]
    if glob is not None:
        command.extend(["--glob", glob])
    command.extend(["--regexp", pattern, search_path])
    search = " ".join(shlex.quote(argument) for argument in command)
    limit = f" | head -n {max_count + 1}" if max_count is not None else ""
    return (
        f"root=$(git -C {probe} rev-parse --show-toplevel 2>/dev/null) || exit 127; "
        'root=$(realpath "$root") || exit 127; '
        r'key=$(printf %s "$root" | sha256sum | cut -d\  -f1); '
        'index="/opt/open-swe/tgrep-indexes/$key"; '
        'test -x /usr/local/bin/tgrep -a -f "$index/lookup.bin" || exit 127; '
        'test -f "$index/serve.json" || exit 127; '
        f"printf '{_START}\\n'; "
        f'{search} --index-path "$index" 2>/dev/null '
        f'| awk \'index($0, "\\"type\\":\\"match\\"")\''
        f"{limit}; printf '{_END}\\n'"
    )


def parse_tgrep_result(output: str, max_count: int | None = None) -> GrepResult | None:
    """Return an indexed result, or `None` when regular grep should run."""
    lines = output.splitlines()
    try:
        start = lines.index(_START)
        end = lines.index(_END, start + 1)
    except ValueError:
        return None
    matches: list[GrepMatch] = []
    try:
        for line in lines[start + 1 : end]:
            frame = json.loads(line)
            data = frame["data"]
            matches.append(
                {
                    "path": data["path"]["text"],
                    "line": data["line_number"],
                    "text": data["lines"]["text"].rstrip("\n"),
                }
            )
    except KeyError, TypeError, json.JSONDecodeError:
        logger.warning("Ignoring malformed tgrep sandbox response")
        return None
    truncated = max_count is not None and len(matches) > max_count
    return GrepResult(
        matches=matches[:max_count] if max_count is not None else matches,
        truncated=truncated,
    )
