"""Tool: ``code_channel_set_view``. Creates or replaces a view in the review guide's code channel."""

from typing import Any

from agent.review_guide.context import GuideContext, GuideUnavailableError
from agent.review_guide.git import GuideGitError, read_file
from agent.slack.code_channels import CanvasAccessLevel, ViewType, set_view


async def code_channel_set_view(
    view_type: ViewType,
    view_key: str = "",
    name: str = "",
    content: str = "",
    file_path: str = "",
    blocks: list[dict[str, Any]] | None = None,
    canvas_id: str = "",
    access_level: CanvasAccessLevel = "write",
    base_branch: str = "",
    head_branch: str = "",
    csp: dict[str, list[str]] | None = None,
    agent_content_hash: str = "",
) -> dict[str, Any]:
    """Implement the `code_channel_set_view` tool."""
    if content and file_path:
        return {"success": False, "error": "pass content or file_path, not both"}
    try:
        ctx = await GuideContext.current()
        if file_path:
            content = await read_file(ctx.backend, file_path)
    except (GuideUnavailableError, GuideGitError) as exc:
        return {"success": False, "error": str(exc)}
    data, error = await set_view(
        ctx.session.slack_channel_id,
        view_type,
        view_key=view_key,
        content=content,
        blocks=blocks,
        canvas_id=canvas_id,
        access_level=access_level,
        base_branch=base_branch,
        head_branch=head_branch,
        name=name,
        csp=csp,
        agent_content_hash=agent_content_hash,
    )
    if error:
        return {"success": False, "error": error}
    return {"success": True, "slack_response": data}
