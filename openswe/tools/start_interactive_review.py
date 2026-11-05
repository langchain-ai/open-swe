from typing import Any

from openswe.audit_logs.tools import audit_tool
from openswe.github.pull_request_context import parse_pull_request_url
from openswe.review_guide.launch import GuideOrigin, GuideStart, GuideStartError, start_review_guide
from openswe.run_config import RunConfig
from openswe.tools.mcp_exposure import expose_mcp


@expose_mcp()
@audit_tool()
async def start_interactive_review(
    pr_url: str, code_channel: bool = False, invite: list[str] | None = None
) -> dict[str, Any]:
    """Implement the `start_interactive_review` tool."""
    parsed = parse_pull_request_url(pr_url)
    if parsed is None:
        return {"success": False, "error": "pr_url must be a GitHub pull request URL"}
    owner, repo, number = parsed
    try:
        origin = await GuideOrigin.for_run(RunConfig.from_runtime())
        started = await start_review_guide(
            GuideStart(
                owner=owner,
                repo=repo,
                number=number,
                origin=origin,
                # Over MCP there is no conversation to review in.
                code_channel=code_channel or origin.source_thread_id is None,
                invite=invite or [],
            )
        )
    except GuideStartError as exc:
        return {"success": False, "error": str(exc)}
    if not started.channel_id:
        return {"success": True, "where": "this conversation"}
    return {
        "success": True,
        "where": "code channel",
        "channel_id": started.channel_id,
        "channel_link": f"<#{started.channel_id}>",
        "channel_url": f"https://slack.com/archives/{started.channel_id}",
    }
