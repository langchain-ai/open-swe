"""Start the image assessment on MDA after a dashboard capture finishes."""

import logging
from uuid import NAMESPACE_URL, uuid5

from openswe.dispatch import create_durable_run, dispatch_client
from openswe.input_messages import build_run_input
from openswe.invocation import new_invocation_id, with_invocation_id
from openswe.prompts import prompt
from openswe.threads.creation import create_thread

from .models import FrontendReviewRun
from .service import set_assessment_state

logger = logging.getLogger(__name__)
_SENDER_ID = "system:frontend-reviewer"


async def launch_assessment(review: FrontendReviewRun, login: str) -> None:
    if review.status != "complete" or not review.report:
        return
    thread_id = str(uuid5(NAMESPACE_URL, f"open-swe:frontend-review:{review.id}"))
    await set_assessment_state(review, "running", thread_id=thread_id)
    try:
        client = dispatch_client()
        await create_thread(
            client,
            thread_id,
            title=f"Frontend review: {review.owner}/{review.repo}#{review.pr_number}",
            if_exists="do_nothing",
            metadata={
                "kind": "frontend-reviewer",
                "source": "dashboard",
                "owner_login": login,
                "visibility": "private",
                "unlisted": True,
                "github_login": login,
                "pr": {"owner": review.owner, "name": review.repo, "number": review.pr_number},
            },
        )
        configurable: dict[str, object] = {
            "thread_id": thread_id,
            "repo": {"owner": review.owner, "name": review.repo},
            "pr_number": review.pr_number,
            "pr_url": f"https://github.com/{review.owner}/{review.repo}/pull/{review.pr_number}",
            "base_sha": review.base_sha,
            "head_sha": review.head_sha,
            "github_login": login,
            "source": "frontend-review",
            "frontend_review_id": str(review.id),
            "reviewer_event": "frontend",
        }
        await create_durable_run(
            thread_id,
            "reviewer",
            input=build_run_input(
                prompt("frontend-review/kickoff", review_id=review.id),
                {"sender_id": _SENDER_ID, "surface": "automation", "kind": "system"},
                systems=[
                    {
                        "id": _SENDER_ID,
                        "display_name": "Frontend reviewer",
                        "platform": "open-swe",
                    }
                ],
            ),
            source="frontend-review",
            thread_title=None,
            config={"configurable": with_invocation_id(configurable, new_invocation_id())},
            metadata={"frontend_review_id": str(review.id)},
            client=client,
            multitask_strategy="reject",
        )
    except Exception:
        logger.exception(
            "Could not start frontend image assessment",
            extra={"frontend_review_id": str(review.id)},
        )
        await set_assessment_state(
            review,
            "failed",
            thread_id=thread_id,
            error="The AI assessment could not start. The captured evidence is still available.",
        )
