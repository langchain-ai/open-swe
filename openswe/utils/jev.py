"""Jev (``runtime_api.classifier``) over the backend's pooled connections."""

from collections.abc import Mapping

from langchain_typesafe import Choice
from runtime_api import classifier
from runtime_api.classifier import JevDecision, SystemOneGateway

from openswe.utils.http_pool import pooled_transport

__all__ = ["JevDecision", "SystemOneGateway", "select_jev_choice", "select_jev_choices"]


async def select_jev_choice[ChoiceT: str](
    task: str,
    *,
    question: str,
    instructions: str,
    criteria: Mapping[ChoiceT, str],
    decision: JevDecision | None = None,
) -> ChoiceT | None:
    return await classifier.select_jev_choice(
        task,
        question=question,
        instructions=instructions,
        criteria=criteria,
        decision=decision,
        transport=pooled_transport(),
    )


async def select_jev_choices(
    task: str,
    *,
    questions: Mapping[str, Choice],
    decisions: Mapping[str, JevDecision | None] | None = None,
) -> dict[str, str | None]:
    return await classifier.select_jev_choices(
        task, questions=questions, decisions=decisions, transport=pooled_transport()
    )
