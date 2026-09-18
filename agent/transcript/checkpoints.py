"""The commit a turn leaves behind in its sandbox.

At the end of every turn the sandbox's whole working tree is written into a
commit object that no branch points at — HEAD, the index and the working tree
are never touched — and a hidden ref, ``refs/open-swe/checkpoints/<thread>/turn/<turn id>``,
records it. The ref is named by the turn's id rather than by its ordinal, so
two turns checkpointed concurrently can never name the same ref. The tree is
built in a scratch index, so the agent's own git commands cannot collide with
it, and untracked-but-not-ignored files count.

That ref is what a later reader diffs one turn against another with. Our
sandboxes are ephemeral, so the sha is recorded alongside it: the ref resolves
only while the sandbox lives, but the sha still identifies the tree wherever
the checkpoint was pushed or compared against a pull request.

Capture is observability. Every failure is recorded as the checkpoint's own
``status`` and logged; nothing here is ever allowed to fail a run.
"""

import base64
import logging
import re
import shlex
from uuid import UUID

from sqlalchemy import text

from agent.database import postgres
from agent.sandboxes.state import SANDBOX_BACKENDS, SandboxBackendProxy
from agent.transcript.engine import Command
from agent.transcript.events import CheckpointFile, TurnCheckpointCompleted
from agent.utils.turn_checkpoint import (
    MAX_TURN_DIFF_FILES,
    cd_repo_command,
    parse_name_status,
    parse_numstat,
)

logger = logging.getLogger(__name__)

CHECKPOINT_REF_PREFIX = "refs/open-swe/checkpoints"
CAPTURE_TIMEOUT_SECONDS = 30
ERROR_TEXT_CAP = 2_000

_NO_REPOSITORY_EXIT = 3
# A thread id reaches git as part of a ref name, so anything that is not a
# LangGraph id is refused rather than escaped.
_REF_SAFE_THREAD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")

_IDENTITY = (
    "export GIT_AUTHOR_NAME='Open SWE' "
    "GIT_AUTHOR_EMAIL='open-swe@users.noreply.github.com' "
    "GIT_COMMITTER_NAME='Open SWE' "
    "GIT_COMMITTER_EMAIL='open-swe@users.noreply.github.com'"
)


def _live_backend(thread_id: str) -> SandboxBackendProxy | None:
    """The thread's sandbox, only if one is already connected.

    Never the proxy's reconnect path: a turn that has ended is not a reason to
    wake a sandbox back up, and the tree it would come back with is not the
    tree the turn left behind.
    """
    backend = SANDBOX_BACKENDS.get(thread_id)
    return backend if backend is not None and backend.has_backend else None


def checkpoint_ref(thread_id: str, turn_id: UUID) -> str | None:
    """The hidden ref for a turn's checkpoint.

    Named by the turn's id, which is ref-safe and unique, so the ref survives
    the ordinal being resolved later and differently by the append.
    """
    if not _REF_SAFE_THREAD_ID.fullmatch(thread_id):
        return None
    return f"{CHECKPOINT_REF_PREFIX}/{thread_id}/turn/{turn_id}"


async def read_head(thread_id: str) -> str | None:
    """The commit the thread's repository is on right now, if it has one.

    Read at the start of a turn so the first checkpoint of a thread has
    something to diff against: after that the previous turn's checkpoint is the
    better baseline, because the agent may well have committed in between.
    """
    backend = _live_backend(thread_id)
    if backend is None:
        return None
    try:
        response = await backend.aexecute(
            f"{cd_repo_command(None)}; git rev-parse HEAD",
            timeout=CAPTURE_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.debug("Could not read the sandbox head for a turn checkpoint", exc_info=True)
        return None
    head = response.output.strip().splitlines()[-1:] if response.exit_code == 0 else []
    return head[0] if head and re.fullmatch(r"[0-9a-f]{7,64}", head[0]) else None


def _capture_script(*, ref: str, baselines: list[str]) -> str:
    """One shell round trip: commit the worktree, name it, diff it."""
    candidates = " ".join(shlex.quote(baseline) for baseline in baselines) or '""'
    return "\n".join(
        [
            "set -e",
            cd_repo_command(None),
            f"git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit {_NO_REPOSITORY_EXIT}",
            _IDENTITY,
            "I=$(mktemp)",
            "export GIT_INDEX_FILE=$I",
            "if git rev-parse --verify -q HEAD >/dev/null;"
            " then git read-tree HEAD; else git read-tree --empty; fi",
            "git add -A . >/dev/null 2>&1 || true",
            "T=$(git write-tree)",
            'unset GIT_INDEX_FILE; rm -f "$I"',
            'C=$(git commit-tree "$T" -m "open-swe turn checkpoint")',
            f'git update-ref {shlex.quote(ref)} "$C"',
            'echo "commit $C"',
            'B=""',
            f"for candidate in {candidates}; do",
            '  if FOUND=$(git rev-parse --verify -q "$candidate^{tree}"); '
            'then B="$FOUND"; break; fi',
            "done",
            'if [ -n "$B" ]; then',
            '  echo "numstat $(git diff --numstat -z --no-renames "$B" "$T"'
            " | base64 | tr -d '\\n')\"",
            '  echo "namestatus $(git diff --name-status -z --no-renames "$B" "$T"'
            " | base64 | tr -d '\\n')\"",
            "fi",
        ]
    )


def _field(output: str, key: str) -> str:
    """The base64 payload of one ``<key> <payload>`` line of the script's output."""
    for line in output.splitlines():
        if line.startswith(f"{key} "):
            encoded = line[len(key) + 1 :].strip()
            if not encoded:
                return ""
            try:
                return base64.b64decode(encoded).decode("utf-8", errors="replace")
            except ValueError:
                logger.warning("Undecodable turn checkpoint diff field", extra={"field": key})
                return ""
    return ""


def _files(numstat: str, name_status: str) -> list[CheckpointFile]:
    statuses = parse_name_status(name_status)
    return [
        CheckpointFile(
            path=path,
            additions=additions or 0,
            deletions=deletions or 0,
            status="added"
            if statuses.get(path) == "added"
            else "removed"
            if statuses.get(path) == "removed"
            else "modified",
        )
        for path, additions, deletions in parse_numstat(numstat)[:MAX_TURN_DIFF_FILES]
    ]


async def _capture(
    backend: SandboxBackendProxy, event: TurnCheckpointCompleted, baselines: list[str]
) -> TurnCheckpointCompleted:
    try:
        response = await backend.aexecute(
            _capture_script(ref=event.checkpoint_ref, baselines=baselines),
            timeout=CAPTURE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return event.model_copy(
            update={"status": "error", "error": f"{type(exc).__name__}: {exc}"[:ERROR_TEXT_CAP]}
        )
    if response.exit_code == _NO_REPOSITORY_EXIT:
        return event
    if response.exit_code != 0:
        return event.model_copy(
            update={
                "status": "error",
                "error": (response.output.strip() or "git checkpoint failed")[:ERROR_TEXT_CAP],
            }
        )
    commit = ""
    for line in response.output.splitlines():
        if line.startswith("commit "):
            commit = line.removeprefix("commit ").strip()
    if not commit:
        return event.model_copy(
            update={"status": "error", "error": "git commit-tree produced no commit"}
        )
    return event.model_copy(
        update={
            "status": "ready",
            "commit": commit,
            "files": _files(
                _field(response.output, "numstat"), _field(response.output, "namestatus")
            ),
        }
    )


async def _turn_context(thread_id: str, turn_id: UUID) -> tuple[int, str | None, str | None]:
    """``(checkpoint_turn_count, previous_ref, assistant_message_id)`` for a turn.

    The count is 1-based over the thread's checkpointed turns, and is only a
    proposal: ``agent.transcript.projections.resolve`` settles it inside the
    append's transaction, where the thread's lock is held.
    """
    async with postgres.read_only_transaction() as conn:
        result = await conn.execute(
            text(
                """
                SELECT
                    (SELECT checkpoint_turn_count FROM thread_turn_checkpoint
                     WHERE thread_id = :thread_id AND turn_id = :turn_id) AS existing_count,
                    COALESCE((SELECT max(checkpoint_turn_count) FROM thread_turn_checkpoint
                              WHERE thread_id = :thread_id), 0) AS highest_count,
                    (SELECT checkpoint_ref FROM thread_turn_checkpoint
                     WHERE thread_id = :thread_id AND turn_id <> :turn_id
                       AND commit IS NOT NULL
                     ORDER BY checkpoint_turn_count DESC LIMIT 1) AS previous_ref,
                    (SELECT message_id FROM thread_message
                     WHERE thread_id = :thread_id AND turn_id = :turn_id AND role = 'ai'
                     ORDER BY created_at DESC, message_id DESC LIMIT 1) AS assistant_message_id
                """
            ),
            {"thread_id": thread_id, "turn_id": turn_id},
        )
        row = result.mappings().one()
    return (
        row["existing_count"] or row["highest_count"] + 1,
        row["previous_ref"],
        row["assistant_message_id"],
    )


async def checkpoint_command(
    thread_id: str, turn_id: UUID, *, run_id: str | None, start_head: str | None
) -> Command:
    """The ``turn.checkpoint.completed`` command for a turn that just ended.

    A thread with no live sandbox, or one whose sandbox holds no repository,
    is checkpointed as ``missing`` without running git at all: there is nothing
    to record, and a future reader has to be able to tell that apart from a
    turn that was never checkpointed.
    """
    turn_count, previous_ref, assistant_message_id = await _turn_context(thread_id, turn_id)
    ref = checkpoint_ref(thread_id, turn_id)
    event = TurnCheckpointCompleted(
        turn_id=turn_id,
        checkpoint_turn_count=turn_count,
        checkpoint_ref=ref or "",
        status="missing",
        assistant_message_id=assistant_message_id,
    )
    backend = _live_backend(thread_id)
    if ref is None:
        event = event.model_copy(
            update={"status": "error", "error": "thread id is not usable as a git ref"}
        )
    elif backend is not None:
        # The previous turn's checkpoint is the baseline; the head the turn
        # started from covers the thread's very first checkpoint, where there
        # is no previous one and the agent may have committed since.
        baselines = [base for base in (previous_ref, start_head) if base]
        event = await _capture(backend, event, baselines)
    if event.status != "ready":
        logger.info(
            "Turn checkpoint was not captured",
            extra={
                "transcript": {
                    "thread_id": thread_id,
                    "turn_id": str(turn_id),
                    "status": event.status,
                    "error": event.error,
                }
            },
        )
    return Command(
        command_id=f"turn:{turn_id}:checkpoint",
        event=event,
        actor_kind="agent",
        run_id=run_id,
        turn_id=turn_id,
    )
