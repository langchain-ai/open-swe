"""Indexed literal search for sandbox repositories."""

import logging
import shlex

from deepagents.backends.protocol import GrepMatch, GrepResult

logger = logging.getLogger(__name__)
_START = "__OPEN_SWE_TGREP_START__"
_END = "__OPEN_SWE_TGREP_END__"


def build_tgrep_command(
    pattern: str,
    path: str | None,
    glob: str | None,
    max_count: int | None = None,
) -> str:
    """Build an indexed search command with shell-quoted model inputs."""
    search_path = path or "."
    quoted_probe = shlex.quote(search_path if search_path.endswith("/") else f"{search_path}/.")
    options = ["--fixed-strings", "--color", "never"]
    if glob is not None:
        options.extend(["--glob", glob])
    options.extend(["--regexp", pattern, search_path])
    quoted_options = " ".join(shlex.quote(argument) for argument in options)
    limit = f" | head -n {max_count + 1}" if max_count is not None else ""
    return (
        f"root=$(git -C {quoted_probe} rev-parse --show-toplevel 2>/dev/null) || exit 127; "
        'root=$(realpath "$root") || exit 127; '
        r'key=$(printf %s "$root" | sha256sum | cut -d\  -f1); '
        'index="/opt/open-swe/tgrep-indexes/$key"; '
        'test -x /usr/local/bin/tgrep -a -f "$index/lookup.bin" || exit 127; '
        "files=$(mktemp) || exit 127; trap 'rm -f \"$files\"' EXIT; "
        f"/usr/local/bin/tgrep --files-with-matches --null {quoted_options} "
        '--index-path "$index" >"$files" 2>/dev/null; status=$?; '
        "case $status in 0) ;; 1) printf '%s\\n%s\\n' "
        f"'{_START}' '{_END}'; exit;; *) exit $status;; esac; "
        'test "$(tr -cd \'\\0\' <"$files" | wc -c)" -le 256 || exit 75; '
        f"printf '{_START}\\n'; "
        f'xargs -0 grep -ZHnF -- {shlex.quote(pattern)} <"$files" 2>/dev/null'
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
            file_path, separator, remainder = line.partition("\0")
            line_number, colon, text = remainder.removeprefix(":").partition(":")
            if not separator or not colon:
                raise ValueError
            matches.append({"path": file_path, "line": int(line_number), "text": text})
    except TypeError, ValueError:
        logger.warning("Ignoring malformed tgrep sandbox response")
        return None
    truncated = max_count is not None and len(matches) > max_count
    return GrepResult(
        matches=matches[:max_count] if max_count is not None else matches,
        truncated=truncated,
    )
