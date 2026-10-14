"""Where a reviewer eval's sandbox reports its progress, under its run's token.

Outside the dashboard router: the caller is a sandbox, not a browser session.
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from openswe.review.eval_jobs import ReviewerEvalRun
from openswe.review.eval_report import REPORT_PATH, REPORT_TOKEN_HEADER, ReviewerEvalReport

router = APIRouter(tags=["reviewer-eval"])


@router.post(REPORT_PATH, status_code=204)
async def report_reviewer_eval(
    report: ReviewerEvalReport,
    token: Annotated[str, Header(alias=REPORT_TOKEN_HEADER, max_length=512)] = "",
) -> None:
    if not token or not await ReviewerEvalRun.record_report(token, report):
        raise HTTPException(401, "Invalid reviewer eval token")
