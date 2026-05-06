from typing import Any, Literal

Severity = Literal["Low", "Medium", "High", "Critical"]

_VALID_SEVERITIES: frozenset[str] = frozenset({"Low", "Medium", "High", "Critical"})


def _normalize_severity(value: str) -> Severity:
    """Title-case `value` and validate it against the allowed set.

    The model occasionally emits "low"/"HIGH" instead of the title-cased
    canonical form. Normalize before recording so we don't burn an LLM
    turn on a Pydantic ValidationError retry.
    """
    titled = value.strip().title()
    if titled not in _VALID_SEVERITIES:
        valid = ", ".join(sorted(_VALID_SEVERITIES))
        raise ValueError(f"severity must be one of {valid}; got {value!r}")
    return titled  # type: ignore[return-value]


def github_comment(
    file: str,
    line: int,
    body: str,
    severity: str,
) -> dict[str, Any]:
    """Record a single inline review comment on the PR under review.

    Call this tool once per issue you find. Multiple calls are expected — one
    per distinct concern. The eval harness records every github_comment call
    you make and scores them against the PR's golden comments.

    **Do not** use this tool to summarize the PR or make general remarks. Each
    call must point at a specific file and line and describe one concrete
    issue (bug, security concern, perf problem, correctness issue, etc.).

    Args:
        file: Repo-relative path to the file the comment applies to.
        line: 1-based line number in the file.
        body: The review comment text. Be specific about the issue.
        severity: One of "Low", "Medium", "High", "Critical" (case-insensitive).

    Returns:
        {"recorded": True, "file", "line", "severity", "body"}.
    """
    return {
        "recorded": True,
        "file": file,
        "line": line,
        "severity": _normalize_severity(severity),
        "body": body,
    }
