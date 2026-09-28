import asyncio
import logging
from collections.abc import Mapping

import httpx2
from langchain_typesafe import Choice, TypeSafeClassifier

from agent.config import ENV
from agent.utils.gateway import gateway_base_url

logger = logging.getLogger(__name__)

JEV_TIMEOUT_SECONDS = 3.0


async def select_jev_choice[ChoiceT: str](
    task: str, *, question: str, instructions: str, criteria: Mapping[ChoiceT, str]
) -> ChoiceT | None:
    typesafe_key = ENV.TYPESAFE_API_KEY.optional()
    gateway_key = ENV.LANGSMITH_GATEWAY_API_KEY.optional() or ENV.LANGSMITH_API_KEY.optional()
    if not typesafe_key and not gateway_key:
        logger.warning("Jev classification has no API key", extra={"question": question})
        return None
    try:
        async with (
            asyncio.timeout(JEV_TIMEOUT_SECONDS),
            httpx2.AsyncClient(timeout=JEV_TIMEOUT_SECONDS) as client,
        ):
            classifier = TypeSafeClassifier(
                model="jev-1.13.0" if typesafe_key else "typesafe/jev-1.13.0",
                api_key=typesafe_key or gateway_key,
                **({} if typesafe_key else {"base_url": gateway_base_url()}),
                async_client=client,
            )
            response = await classifier.ainvoke(
                {
                    "state": task,
                    "questions": {
                        question: Choice(instructions=instructions, criteria=dict(criteria.items()))
                    },
                },
                config={"tags": ["nostream"]},
            )
            answer = response.choices[question]
        if not 0.6 <= answer.confidence <= 1.0:
            logger.info(
                "Jev classification confidence below threshold or invalid",
                extra={"question": question, "confidence": answer.confidence},
            )
            return None
        for choice in criteria:
            if answer.choice == choice:
                return choice
        raise ValueError("Jev returned a choice outside the supplied criteria")
    except Exception:
        logger.exception("Jev classification failed", extra={"question": question})
        return None
