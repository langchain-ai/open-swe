Ask a message's sender privately whether Open SWE should reply to it.

Only the person who posted the message at `message_ts` sees the question, with a
Reply button and a Dismiss button; nobody else in the channel sees anything. When
they click Reply, you get a new turn asking you to answer that message. Dismiss
ends it quietly.

Use it when you are unsure a message in this Slack thread is meant for you, so the
sender decides instead of you guessing. Pass that message's `timestamp` as
`message_ts`, not the thread's. After it succeeds, post nothing in the thread and end
the turn with `slack_no_reply_needed`.
