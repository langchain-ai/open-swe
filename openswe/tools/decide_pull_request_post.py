"""Act on an escalated triage run's decision about a Slack pull request post."""

from openswe.human_review.post_intent import linked_pull_requests
from openswe.human_review.post_investigation import DECIDING_REACTION
from openswe.human_review.posted import watch_pull_requests
from openswe.run_config import RunConfig
from openswe.slack.client import remove_slack_reaction
from openswe.users import User


async def decide_pull_request_post(review_urls: list[str]) -> dict[str, object]:
    """Implement the `decide_pull_request_post` tool."""
    post = RunConfig.from_runtime().pull_request_post
    if post is None:
        return {"success": False, "error": "This run is not deciding a pull request post."}
    linked = {ref.url.lower(): ref for ref in linked_pull_requests(post.text)}
    chosen = {url.strip().rstrip("/").lower() for url in review_urls}
    unknown = sorted(chosen - linked.keys())
    if unknown:
        return {"success": False, "error": f"Not linked by the post: {', '.join(unknown)}"}
    user = await User.for_identity("slack", post.slack_user_id)
    if user is None:
        return {"success": False, "error": "The poster has no Open SWE account."}
    refs = [ref for url, ref in linked.items() if url in chosen]
    await watch_pull_requests(post.channel_id, post.message_ts, user, refs)
    await remove_slack_reaction(post.channel_id, post.message_ts, DECIDING_REACTION)
    return {"success": True, "watching": [ref.url for ref in refs]}
