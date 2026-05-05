from typing import Any, Literal

Severity = Literal["Low", "Medium", "High", "Critical"]


def github_comment(
    file: str,
    line: int,
    body: str,
    severity: Severity,
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
        severity: One of "Low", "Medium", "High", "Critical".

    Returns:
        {"recorded": True}.
    """
    return {"recorded": True, "file": file, "line": line, "severity": severity, "body": body}
