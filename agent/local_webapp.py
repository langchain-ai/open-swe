"""Desktop-only HTTP endpoints served by the local LangGraph backend."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent.utils.langsmith import create_langsmith_thread_feedback


class LocalThreadFeedback(BaseModel):
    thread_id: str = Field(min_length=1)
    rating: Literal["bad", "good"]
    comment: str = Field(default="", max_length=3000)


app = FastAPI()


@app.post("/open-swe/feedback")
async def submit_local_thread_feedback(feedback: LocalThreadFeedback) -> dict[str, bool]:
    exported = await create_langsmith_thread_feedback(
        feedback.thread_id,
        "rating",
        score=1.0 if feedback.rating == "good" else 0.0,
        comment=feedback.comment.strip() or None,
        source_info={"source": "desktop_thread_feedback"},
    )
    return {"exported": exported}
