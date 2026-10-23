"""Hand a pull request post Jev could not read to an unlisted agent run to decide."""

import logging
import uuid

from openswe.dispatch import dispatch_agent_run
from openswe.human_review.post_intent import linked_pull_requests
from openswe.prompts import prompt
from openswe.run_config import PullRequestPostRef
from openswe.slack.client import add_slack_reaction, remove_slack_reaction
from openswe.threads.runs import build_dashboard_configurable, create_dashboard_thread_record
from openswe.users import User
from openswe.utils.json_types import thread_metadata

logger = logging.getLogger(__name__)

DECIDING_REACTION = "eyes"


async def investigate_post(post: PullRequestPostRef) -> bool:
    """Start the poster's unlisted triage run, marking the post 👀 until it decides; whether it started."""
    login = await User.login_for_slack(post.slack_user_id)
    refs = linked_pull_requests(post.text)
    if not login or not refs:
        return False
    thread_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe:pr-post:{post.channel_id}:{post.message_ts}")
    )
    run_prompt = prompt(
        "runs/pull-request-post-triage",
        channel_id=post.channel_id,
        message_ts=post.message_ts,
        text=post.text,
        urls=[ref.url for ref in refs],
    )
    extra = {"agent_thread_id": thread_id, "slack_channel": post.channel_id}
    await add_slack_reaction(post.channel_id, post.message_ts, DECIDING_REACTION)
    try:
        thread = await create_dashboard_thread_record(
            thread_id,
            login=login,
            repo_config={"owner": refs[0].owner, "name": refs[0].repo},
            prompt=run_prompt,
            title="Decide how to handle a pull request post",
            visibility="private",
            extra_metadata={"unlisted": True},
        )
        configurable = await build_dashboard_configurable(thread_id, login, thread_metadata(thread))
        await dispatch_agent_run(
            thread_id,
            run_prompt,
            {**configurable, "pull_request_post": post.model_dump()},
            source="dashboard",
            thread_title=None,
        )
    except Exception:
        logger.exception("Could not start a pull request post triage run", extra=extra)
        await remove_slack_reaction(post.channel_id, post.message_ts, DECIDING_REACTION)
        return False
    logger.info("Started a pull request post triage run", extra=extra)
    return True
