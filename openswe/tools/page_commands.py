"""The tools a run gets from the dashboard page its message was sent from.

Each tool the page offered queues a command for that browser tab and answers at
once with the command's position in the tab's queue; ``read_page_queue`` reads how
far the tab has carried the queue out and what each command returned.
"""

import json
from typing import Final

from langchain_core.tools import BaseTool, StructuredTool

from openswe.page_commands.offers import PAGE_TOOL_PREFIX, PageSession, PageToolOffer
from openswe.page_commands.records import PageQueue
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.utils.json_types import JsonObject

READ_PAGE_QUEUE: Final = "read_page_queue"


class PageTools:
    """The tools of the page session a run's message came from."""

    def __init__(self, page: PageSession, thread_id: str) -> None:
        self.queue = PageQueue(page.id, page.owner)
        self.thread_id = thread_id
        self.tools: list[BaseTool] = [
            *(self._queues(spec.name, spec.description, spec.parameters) for spec in page.tools),
            self._reader(),
        ]
        # What the run's system prompt says about them, after the rest of it.
        self.instructions = prompt(
            "page-commands/instructions", prefix=PAGE_TOOL_PREFIX, reader=READ_PAGE_QUEUE
        )

    @staticmethod
    def of_run(cfg: RunConfig) -> PageTools | None:
        """The page tools of a run sent from a page that offered some."""
        if cfg.page is None or not cfg.thread_id:
            return None
        return PageTools(cfg.page, cfg.thread_id)

    def tool(self, name: str) -> BaseTool | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    def _queues(self, name: str, description: str, parameters: JsonObject) -> BaseTool:
        page_name = PageToolOffer.page_name(name) or name

        async def queue_command(**arguments: object) -> str:
            position = await self.queue.enqueue(self.thread_id, page_name, dict(arguments))
            return json.dumps({"queued": True, "position": position})

        return StructuredTool(
            name=name, description=description, args_schema=parameters, coroutine=queue_command
        )

    def _reader(self) -> BaseTool:
        async def read_page_queue(since: int = 0, wait_for: int | None = None) -> str:
            progress = await self.queue.progress(self.thread_id, since, wait_for=wait_for)
            return progress.model_dump_json()

        return StructuredTool.from_function(
            coroutine=read_page_queue,
            name=READ_PAGE_QUEUE,
            description=prompt(f"tools/{READ_PAGE_QUEUE}"),
        )
