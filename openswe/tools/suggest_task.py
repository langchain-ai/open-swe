import logging

logger = logging.getLogger(__name__)


async def suggest_task(repo: str, description: str, directories: list[str]) -> dict[str, str]:
    """Implement the `suggest_task` tool."""
    logger.info(
        "Unrelated task suggested",
        extra={
            "suggested_task_repo": repo,
            "suggested_task_description": description,
            "suggested_task_directories": directories,
        },
    )
    return {"status": "logged_only"}
