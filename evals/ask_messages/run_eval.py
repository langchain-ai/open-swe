"""Score Jev's Ask-channel decisions against labelled #ask-robin messages.

Usage:
    LANGSMITH_TRACING=false uv run python -m evals.ask_messages.run_eval

Each example is a real Ask-channel conversation ending in the untagged message Jev judges.
The target runs the production prompt path (`AskChannelMessage`) against Jev, so an
experiment scores the prompts at the checked-out commit.
"""

import argparse
import asyncio
import subprocess

from langsmith import Client, aevaluate
from langsmith.schemas import Example, Run

from openswe.slack import ask_messages
from openswe.slack.ask_messages import AskChannelMessage
from openswe.slack.payloads import SlackChannelContext, SlackMessage
from openswe.utils.json_types import JsonObject

DATASET_NAME = "robin-ask-messages"


async def judge(inputs: JsonObject) -> JsonObject:
    """What Jev says Robin should do with the conversation's last message, through production."""
    # Every example names the same bot, so setting it per call is safe under concurrency.
    ask_messages.common.SLACK_BOT_USER_ID = str(inputs["bot_user_id"])
    raw_messages = inputs["messages"]
    raw_names = inputs["user_names"]
    raw_channel = inputs["channel"]
    assert isinstance(raw_messages, list) and isinstance(raw_names, dict)
    assert isinstance(raw_channel, dict)
    messages = [
        m for m in (SlackMessage.parse(r) for r in raw_messages if isinstance(r, dict)) if m
    ]
    response = await AskChannelMessage.judge(
        messages,
        {str(k): str(v) for k, v in raw_names.items()},
        SlackChannelContext.model_validate(raw_channel),
    )
    return {
        "follow_up": "follow_up" if response.follows_up else "no_follow_up",
        "follow_up_probability": response.follow_up,
        "action": response.action,
        "confidence": response.confidence,
        "reaction": response.reaction,
        "unavailable": response.unavailable,
    }


def follow_up_correct(run: Run, example: Example) -> JsonObject:
    outputs = run.outputs or {}
    expected = (example.outputs or {})["expected"]
    return {
        "key": "follow_up_correct",
        "score": outputs.get("follow_up") == expected,
        "comment": f"expected {expected}; follow-up probability "
        f"{outputs.get('follow_up_probability')}, unavailable {outputs.get('unavailable')}",
    }


def follow_up_summary(runs: list[Run], examples: list[Example]) -> list[JsonObject]:
    pairs = [
        ((run.outputs or {}).get("follow_up"), (example.outputs or {})["expected"])
        for run, example in zip(runs, examples, strict=True)
    ]
    true_pos = sum(got == want == "follow_up" for got, want in pairs)
    predicted = sum(got == "follow_up" for got, _ in pairs)
    actual = sum(want == "follow_up" for _, want in pairs)
    return [
        {"key": "accuracy", "score": sum(got == want for got, want in pairs) / len(pairs)},
        {"key": "follow_up_precision", "score": true_pos / predicted if predicted else 0.0},
        {"key": "follow_up_recall", "score": true_pos / actual if actual else 0.0},
    ]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Score Jev's Ask-channel decisions.")
    parser.add_argument("--dataset", default=DATASET_NAME)
    parser.add_argument("--experiment-prefix", default="ask-messages")
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--split",
        choices=("tune", "held_out"),
        help="Score one split: tune prompts on `tune`, then check them once on `held_out`.",
    )
    args = parser.parse_args()
    revision = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    results = await aevaluate(
        judge,
        data=Client().list_examples(dataset_name=args.dataset, splits=[args.split])
        if args.split
        else args.dataset,
        evaluators=[follow_up_correct],
        summary_evaluators=[follow_up_summary],
        experiment_prefix=f"{args.experiment_prefix}-{revision or 'unknown'}",
        metadata={"revision": revision},
        max_concurrency=args.max_concurrency,
        num_repetitions=args.repetitions,
    )
    print(results.experiment_name)


if __name__ == "__main__":
    asyncio.run(main())
