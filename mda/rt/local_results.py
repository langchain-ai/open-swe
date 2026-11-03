"""What the backend's tools that read only their arguments answer, as MDA runs them.

MDA runs these tools itself (``rt.local_tools``), and the model, the reply rules and the
review page read their results, so each answers byte for byte as the backend's does.
Standard library only, so ``tests/agent/test_mda_local_results.py`` holds it to the
backend's tools.
"""

import json
from typing import Final, Literal

DiffSide = Literal["LEFT", "RIGHT"]
ReviewEvent = Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"]

MAX_BODY_CHARS: Final = 10_000
MAX_SUMMARY_CHARS: Final = 2_000
MAX_EXIT_CODE: Final = 255
MAX_INLINE_CHARS: Final = 100_000
OUTPUT_CHUNK_CHARS: Final = 500
REQUIRED_CONFIRMATION: Final = "The user cannot see anything I do not send to Slack."


def _normalized(text: str) -> str:
    return " ".join(text.split()).rstrip(".").casefold()


def slack_no_reply_needed(reason: str, confirmation: str, *, next_step: str) -> dict[str, object]:
    """The turn closed without a reply, or why it did not; ``next_step`` closes it."""
    if not reason.strip():
        return {
            "success": False,
            "error": "reason is required",
            "hint": "State in one sentence why this turn warrants no reply.",
        }
    if _normalized(confirmation) != _normalized(REQUIRED_CONFIRMATION):
        return {
            "success": False,
            "error": "confirmation does not match",
            "hint": (
                f"Type exactly: {REQUIRED_CONFIRMATION} If the asker is waiting on anything, "
                "send it with `slack_reply` instead."
            ),
        }
    return {"success": True, "turn_closed": True, "next_step": next_step}


def suggest_task(repo: str, description: str, directories: list[str]) -> dict[str, str]:  # noqa: ARG001
    return {"status": "logged_only"}


def cli_result(stdout: str, exit_code: int, unchanged: bool = False) -> dict[str, bool | str]:  # noqa: ARG001, FBT001, FBT002
    if not 0 <= exit_code <= MAX_EXIT_CODE:
        return {"success": False, "error": f"exit_code must be between 0 and {MAX_EXIT_CODE}"}
    return {"success": True}


def record_human_input(summary: str) -> tuple[str, str]:
    """The result's text, and the summary the run's state keeps."""
    trimmed = summary.strip()[:MAX_SUMMARY_CHARS]
    return ("Recorded." if trimmed else "summary is empty; nothing recorded"), trimmed


def _range(file: str, line: int, start_line: int | None, side: DiffSide) -> dict[str, object]:
    path = file.strip().lstrip("/")
    if not path:
        raise ValueError("file must be a path from the diff")
    start = start_line if start_line is not None else line
    if start < 1 or line < start:
        raise ValueError("start_line must be at least 1 and line must not precede it")
    return {"file": path, "start_line": start, "end_line": line, "side": side}


def _comment_text(body: str) -> str:
    text = body.strip()
    if not text:
        raise ValueError("body must not be empty")
    if len(text) > MAX_BODY_CHARS:
        raise ValueError(f"body must be at most {MAX_BODY_CHARS} characters")
    return text


def note_on_diff(
    file: str, line: int, body: str, start_line: int | None = None, side: DiffSide = "RIGHT"
) -> dict[str, object]:
    try:
        return {
            "noted": True,
            "range": _range(file, line, start_line, side),
            "body": _comment_text(body),
        }
    except ValueError as exc:
        return {"noted": False, "error": str(exc)}


def propose_review_comment(
    file: str, line: int, body: str, start_line: int | None = None, side: DiffSide = "RIGHT"
) -> dict[str, object]:
    try:
        lines = _range(file, line, start_line, side)
        text = _comment_text(body)
    except ValueError as exc:
        return {"proposed": False, "error": str(exc)}
    return {
        "proposed": True,
        "range": lines,
        "body": text,
        "note": (
            "Shown to the user as a draft. If they accept it, it joins their pending"
            " GitHub review, which posts only when they submit the review."
        ),
    }


def propose_pr_review(event: ReviewEvent, body: str = "") -> dict[str, object]:
    text = body.strip()
    if event != "APPROVE" and not text:
        return {"proposed": False, "error": "a comment or change request needs a body"}
    if len(text) > MAX_BODY_CHARS:
        return {"proposed": False, "error": f"body must be at most {MAX_BODY_CHARS} characters"}
    return {
        "proposed": True,
        "event": event,
        "body": text,
        "note": "Shown to the user as a draft. Nothing is submitted until they confirm it.",
    }


def chunk_output_as_jsonl(content: str) -> str:
    """``content`` as the JSON lines a large result is offloaded as, in chunks."""
    records = (
        {"chunk": index, "text": content[offset : offset + OUTPUT_CHUNK_CHARS]}
        for index, offset in enumerate(range(0, max(len(content), 1), OUTPUT_CHUNK_CHARS), start=1)
    )
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records)


def bounded_inline_results(results: str) -> str:
    """Search results too large to keep inline, cut to what is."""
    if len(results) <= MAX_INLINE_CHARS:
        return results
    return (
        results[:MAX_INLINE_CHARS]
        + "\n... [results truncated: "
        + f"{MAX_INLINE_CHARS}/{len(results)} chars]\n"
    )


def truncated_markdown(markdown: str) -> str:
    """A fetched page's markdown, cut to what a result keeps."""
    if len(markdown) <= MAX_INLINE_CHARS:
        return markdown
    return (
        markdown[:MAX_INLINE_CHARS] + "\n... [content truncated: "
        f"{MAX_INLINE_CHARS}/{len(markdown)} chars]\n"
    )
