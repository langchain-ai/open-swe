"""The live transcript of runs hosted on another runtime, behind the run token.

``POST /remote-runtime/transcript`` takes the fragments a remote run streamed
(``openswe_transcript.remote``) and appends them to the run's open turn. It
serves every graph alike, since a transcript does not depend on the graph. The
thread comes from the verified token, never from the body.
"""

import logging
from typing import Final

from openswe_transcript.wire import FragmentBatch
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from openswe.remote_runtime.server import RunTokenMiddleware, verified_run
from openswe.transcript.fragments import FragmentsRefused, append_fragments

logger = logging.getLogger(__name__)

# A full batch of hard-flushed fragments, with room to spare.
_MAX_BODY_BYTES: Final = 8 * 1024 * 1024


async def _read_body(request: Request) -> bytes | None:
    """The request body, or ``None`` once it outgrows ``_MAX_BODY_BYTES``."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_BODY_BYTES:
            return None
    return bytes(body)


async def _transcript_endpoint(scope: Scope, receive: Receive, send: Send) -> None:
    run = verified_run(scope)
    body = await _read_body(Request(scope, receive))
    if body is None:
        response = JSONResponse({"detail": "The batch is too large"}, status_code=413)
        await response(scope, receive, send)
        return
    try:
        batch = FragmentBatch.model_validate_json(body)
    except ValidationError as exc:
        response = JSONResponse(
            {
                "detail": "The body is not a fragment batch",
                "errors": exc.errors(include_url=False, include_context=False, include_input=False),
            },
            status_code=422,
        )
        await response(scope, receive, send)
        return
    try:
        appended = await append_fragments(run.thread_id, batch)
    except FragmentsRefused as exc:
        logger.info(
            "Remote transcript fragments refused",
            extra={
                "refusal": str(exc),
                "thread_id": run.thread_id,
                "transcript_run_id": batch.run_id,
            },
        )
        await JSONResponse({"detail": str(exc)}, status_code=409)(scope, receive, send)
        return
    except Exception:
        logger.exception(
            "Appending remote transcript fragments failed",
            extra={"thread_id": run.thread_id, "transcript_run_id": batch.run_id},
        )
        raise
    await JSONResponse({"appended": appended})(scope, receive, send)


TRANSCRIPT: Final = RunTokenMiddleware(_transcript_endpoint)
