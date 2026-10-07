import json
import logging

from langchain.agents.middleware import AgentState, before_model
from langchain_core.messages import HumanMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from openswe.input_messages import input_message_text, message_sender_id, system_input
from openswe.middleware.trace import scrub_middleware_inputs
from openswe.prompts import prompt
from openswe.slack.pr_links import linked_pull_request_urls
from openswe.tools.threads import list_threads

logger = logging.getLogger(__name__)


@scrub_middleware_inputs
@before_model
async def pr_thread_reminder_before_model(
    state: AgentState, runtime: Runtime
) -> dict[str, list[HumanMessage]] | None:
    messages = state["messages"]
    human = next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, HumanMessage)
            and message_sender_id(message.content, kind="human")
        ),
        None,
    )
    if human is None:
        return None
    reminder_id = f"pr-thread-reminder:{human.id}"
    if any(message.id == reminder_id for message in messages):
        return None
    urls = linked_pull_request_urls(input_message_text(human.content) or "")
    if not urls:
        return None
    current_thread = get_config().get("configurable", {}).get("thread_id")
    targets: list[dict[str, str]] = []
    try:
        for url in sorted(urls):
            offset = 0
            while True:
                page = await list_threads(query=url, offset=offset, state=dict(state))
                if not page.get("success"):
                    logger.warning("PR thread reminder lookup failed", extra={"pr_url": url})
                    break
                items = page.get("items")
                if not isinstance(items, list):
                    break
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    thread_id = item.get("id")
                    if not isinstance(thread_id, str) or thread_id == current_thread:
                        continue
                    target = {"pr_url": url, "thread_id": thread_id}
                    for key in ("title", "webUrl", "slackUrl"):
                        value = item.get(key)
                        if isinstance(value, str):
                            target[key] = value
                    targets.append(target)
                if not page.get("has_more"):
                    break
                offset += 25
    except Exception:
        logger.warning("PR thread reminder lookup failed", exc_info=True)
        return None
    if not targets:
        return None
    content = system_input(
        prompt("runs/pr-thread-reminder", targets=json.dumps(targets, ensure_ascii=True)),
        {
            "sender_id": "system:pr-thread-reminder",
            "surface": "automation",
            "kind": "system",
        },
    )["content"]
    assert isinstance(content, str)
    return {"messages": [HumanMessage(content=content, id=reminder_id)]}
