# Ask-channel message eval

LangSmith eval for how Jev decides whether Robin follows up on an untagged message in an Ask channel (`openswe/slack/ask_messages.py`).

## Dataset

`robin-ask-messages` holds real #ask-robin conversations, each ending in the untagged message Jev judges.
It lives only in LangSmith because it contains internal Slack messages; never commit examples to this repository.

Each example's inputs mirror what `AskChannelMessage` reads from Slack:

- `channel`: the channel's `name`, `topic` and `purpose`.
- `messages`: Slack message payloads, oldest first; the last one is judged.
  Thread replies carry `thread_ts`; Robin's own messages carry `bot_user_id` as `user`.
- `user_names`: the display name for each Slack user ID.
- `bot_user_id`: Robin's Slack user ID.

`outputs.expected` is `follow_up` when Robin should reply or act, or `no_follow_up` when it should stay quiet or at most react.
Ambiguous messages and messages with attachments, which skip Jev, are left out.

## Run

Use a LangSmith key that can read the dataset and has `gateway:invoke`:

```bash
LANGSMITH_TRACING=false uv run python -m evals.ask_messages.run_eval
```

The experiment is named after the checked-out commit, so comparing two experiments compares two prompt revisions.
`follow_up_correct` scores each example, and the summary reports accuracy and follow-up precision and recall.
Jev's answers vary slightly between calls; pass `--repetitions 3` before trusting a small difference.

The dataset has two splits, `tune` (100 examples) and `held_out` (53), with every message of a thread on the same side.
Change prompts against `--split tune`, then check the result once with `--split held_out`; a gain on `tune` alone is usually overfitting.
Differences of one to three examples are within Jev's run-to-run noise at this size.
