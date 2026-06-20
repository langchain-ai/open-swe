"""Diff grouping for the reviewer "AI sorted" view.

A lightweight, single structured-output LLM pass that partitions a PR's
changed files into ordered, logically-connected groups, each with a short
headline and explanation. The pass is kicked off concurrently with the
reviewer run (so it adds ~0 latency) and its result is stored on the reviewer
thread metadata under ``diff_groups`` for the review UI to render as a
top-to-bottom walkthrough of the PR.

Everything here is best-effort: any failure logs and degrades to "no groups",
and the UI falls back to the folder/flat view. The grouping never blocks or
breaks the review itself.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from .reviewer_diff import parse_unified_diff
from .reviewer_findings import get_thread_metadata, set_reviewer_thread_metadata

logger = logging.getLogger(__name__)

# Bound the prompt so a huge PR can't blow up the cost/latency of the grouping
# pass. The full changed-file list is always included (so every file can be
# assigned); only the per-file hunk previews are budgeted.
MAX_PROMPT_CHARS = 48_000
MAX_FILE_HUNK_CHARS = 4_000
MAX_GROUPS = 8


class DiffGroup(TypedDict):
    """One logical group of changed files for the AI sorted view."""

    title: str
    summary: str
    files: list[str]


class _DiffGroupModel(BaseModel):
    title: str = Field(
        description=(
            "Short headline naming the logical change, roughly 4-10 words, no "
            "trailing punctuation. Wrap code identifiers in `backticks`."
        )
    )
    summary: str = Field(
        description=(
            "A short, plain explanation of what this group changes and why, in a "
            "few sentences a reviewer can skim. Wrap code identifiers, symbols, "
            "types, flags, and paths in `backticks`. No code blocks or links."
        )
    )
    files: list[str] = Field(
        description="Changed file paths in this group, copied verbatim, in reading order."
    )


class _DiffGroupingResult(BaseModel):
    groups: list[_DiffGroupModel] = Field(
        description="Ordered groups forming a top-to-bottom walkthrough of the PR."
    )


_PROMPT_TEMPLATE = """You are organizing a GitHub pull request's changed files into a clear, \
top-to-bottom walkthrough for a human reviewer.

Group the changed files by logical intent — put files that implement one \
coherent change together (e.g. "the new feature", "the supporting refactor", \
"the config wiring", "the tests"). Order the groups so a reviewer can read \
them top to bottom and build a mental model of the PR.

Rules:
- Assign every changed file to exactly one group.
- Use at most {max_groups} groups. Prefer fewer, larger groups over many tiny ones.
- title: a short headline naming the change, roughly 4-10 words, no trailing \
punctuation. Wrap code identifiers (symbols, flags, file names) in `backticks`.
- summary: a short, plain explanation of what the group changes and why, that a \
reviewer can skim:
    - Keep it focused — a few short sentences. Do not restate the diff line by line.
    - Wrap every code identifier, symbol, type, flag, and path in `backticks`.
    - Plain prose only — no code blocks and no links.
- files: the exact file paths (copied verbatim from the list below) in this \
group, in the order a reviewer should read them.

Changed files:
{file_list}

Diffs:
{diffs}
"""


def diff_signature(diff_text: str) -> str:
    """Stable content hash of the diff, used to skip regeneration when unchanged."""
    return hashlib.sha256(diff_text.encode("utf-8", "ignore")).hexdigest()


def _changed_files(diff_text: str) -> list[str]:
    return [fd.file for fd in parse_unified_diff(diff_text)]


def _build_prompt(diff_text: str, files: list[str]) -> str:
    parts: list[str] = []
    budget = MAX_PROMPT_CHARS
    for file_diff in parse_unified_diff(diff_text):
        segments: list[str] = []
        for hunk in file_diff.hunks:
            body = hunk.body
            if len(body) > MAX_FILE_HUNK_CHARS:
                body = body[:MAX_FILE_HUNK_CHARS] + "\n... (truncated)"
            segments.append(f"```diff\n{body}\n```")
        block = f"### {file_diff.file}\n" + "\n".join(segments) + "\n"
        if budget - len(block) < 0:
            parts.append(f"### {file_diff.file}\n(diff omitted — prompt budget reached)\n")
            continue
        budget -= len(block)
        parts.append(block)
    file_list = "\n".join(f"- {path}" for path in files)
    return _PROMPT_TEMPLATE.format(
        max_groups=MAX_GROUPS,
        file_list=file_list,
        diffs="\n".join(parts),
    )


def _normalize_groups(result: _DiffGroupingResult, files: list[str]) -> list[DiffGroup]:
    """Validate the model output into a clean partition.

    Each file is assigned at most once (first group wins), unknown paths are
    dropped, empty/untitled groups are dropped, and the group count is capped.
    Files the model failed to assign are intentionally left out here — the UI
    collects them into a trailing "Other changes" group, which also keeps the
    view robust against stale group data.
    """
    valid = set(files)
    seen: set[str] = set()
    groups: list[DiffGroup] = []
    for group in result.groups:
        title = (group.title or "").strip()
        summary = (group.summary or "").strip()
        picked: list[str] = []
        for path in group.files:
            if path in valid and path not in seen:
                seen.add(path)
                picked.append(path)
        if not title or not picked:
            continue
        groups.append({"title": title, "summary": summary, "files": picked})
        if len(groups) >= MAX_GROUPS:
            break
    return groups


async def generate_diff_groups(
    *,
    diff_text: str,
    model: BaseChatModel,
) -> list[DiffGroup] | None:
    """Run the single structured-output grouping pass over a unified diff.

    Returns the normalized groups, ``[]`` when there are no changed files, or
    ``None`` when the LLM call fails (caller treats ``None`` as "leave existing
    groups untouched").
    """
    files = _changed_files(diff_text)
    if not files:
        return []
    prompt = _build_prompt(diff_text, files)
    try:
        structured = model.with_structured_output(_DiffGroupingResult)
        result = await structured.ainvoke(prompt)
    except Exception:  # noqa: BLE001 — grouping is best-effort, never break the review
        logger.exception("Diff grouping LLM call failed")
        return None
    if not isinstance(result, _DiffGroupingResult):
        logger.warning("Diff grouping returned unexpected type: %s", type(result))
        return None
    return _normalize_groups(result, files)


async def maybe_generate_and_store_diff_groups(
    *,
    thread_id: str,
    head_sha: str,
    diff_text: str,
    model: BaseChatModel,
) -> None:
    """Generate diff groups and persist them on the reviewer thread metadata.

    Skips regeneration when the persisted signature already matches the current
    diff (the cheap no-op path for re-reviews with no file changes). All errors
    are logged and swallowed so this can run as a fire-and-forget background
    task without ever affecting the review.
    """
    try:
        if not thread_id or not diff_text:
            return
        signature = diff_signature(diff_text)
        metadata = await get_thread_metadata(thread_id)
        existing = metadata.get("diff_groups") if isinstance(metadata, dict) else None
        if (
            isinstance(existing, dict)
            and existing.get("signature") == signature
            and isinstance(existing.get("groups"), list)
            and existing.get("groups")
        ):
            return
        groups = await generate_diff_groups(diff_text=diff_text, model=model)
        if groups is None:
            return
        payload = {
            "head_sha": head_sha,
            "signature": signature,
            "generated_at": datetime.now(UTC).isoformat(),
            "groups": groups,
        }
        await set_reviewer_thread_metadata(thread_id, extra={"diff_groups": payload})
        logger.info(
            "Stored %d diff group(s) for reviewer thread %s (head %s)",
            len(groups),
            thread_id,
            head_sha[:7] if head_sha else "?",
        )
    except Exception:  # noqa: BLE001 — best-effort; never break the review
        logger.exception("Failed to generate/store diff groups for thread %s", thread_id)
