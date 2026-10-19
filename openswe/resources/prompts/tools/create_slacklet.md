Build your own slacklet: a live message in this Slack thread whose state, commands and look are
defined by a small JavaScript program you write. Use it to show work in progress the people in
this thread will want to watch, such as a checklist, a rollout tracker, a poll tally or a
migration's progress. For a pull request use `start_slacklet` instead.

`program` must define three pure functions:

```js
function init(params) { return { items: params.items, done: [] }; }          // the first state
function apply(state, command) {                                          // command = {op, params}
  if (command.op === "check") return { ...state, done: [...state.done, command.params.item] };
  throw new Error(`unknown op ${command.op}`);                            // throw to refuse
}
function render(state) {                                                  // the message
  return {
    text: `${state.done.length}/${state.items.length} done`,             // notification text
    blocks: [
      { type: "section", text: { type: "mrkdwn", text: "*Release checklist*" } },
      { type: "section", text: { type: "mrkdwn",
        text: state.items.map(i => `${state.done.includes(i) ? "✅" : "⬜"} ${i}`).join("\n") } },
      { type: "context", elements: [{ type: "mrkdwn", text: "Updated by Open SWE" }] },
    ],
  };
}
```

Rules:
- `state` and every return value must be plain JSON. There is no `Date`, `Math.random`, network,
  filesystem or timer, and each call has 0.5s and 16 MB: replaying the commands must always
  rebuild the same state, so pass times and other outside facts in `command.params`.
- `render` may only use `section`, `context` and `divider` blocks with `mrkdwn` or `plain_text`
  text: they render the same in Slack and the web UI. At most 40 blocks; text up to 3000 characters.
- `params` is passed to `init`. `create_slacklet` runs `init` and `render` before posting, and
  returns the error instead when the program fails.

Change it later with `update_slacklet`. Each applied command is stored permanently, shown in the
web UI's command log and returned to you; only the slacklet message changes in Slack. Calling
`create_slacklet` again with the same program in this thread returns the existing slacklet.
The slacklet is itself a reply: finish with `slack_no_reply_needed` unless there is more to say.
