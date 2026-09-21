import { test, expect, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  OTHER_USER,
  SAME_ORIGIN_HEADERS,
  SAME_USER,
  loginAs,
  typeIntoComposer,
} from "./helpers/dashboard";

// What the model receives as people come and go in a thread, recorded off the
// real server: Slack webhooks, the dashboard composer and the settings API
// drive every turn, and the dump below is every message the run was handed.
// `thread_context.md` next to this file is that record, reviewable line by
// line; regenerate it with UPDATE_THREAD_CONTEXT_FIXTURE=1.
const FIXTURE = resolve(__dirname, "thread_context.md");

const ALICE_SLACK = "U_ALICE";
const BOB_SLACK = "U_BOB";
// Nobody has ever linked this Slack account to Open SWE.
const CAROL_SLACK = "U_CAROL";

const BOB_INSTRUCTIONS =
  "Never use ripgrep.\nRun `make lint` before every push.";

type MessageContent = string | Array<{ text?: string }>;

interface StateMessage {
  type?: string;
  content?: MessageContent;
}

interface ThreadRun {
  status?: string;
  kwargs?: { input?: { messages?: StateMessage[] } };
}

interface SlackSend {
  thread_ts: string;
  thread_id: string;
  webhook: { status: string };
}

interface Turn {
  title: string;
  given: string;
  when: string;
  outcome: string;
  ran: boolean;
  dispatch: string[];
  run: string[];
}

function contentText(message: StateMessage): string {
  const content = message.content;
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .flatMap((block) => (typeof block.text === "string" ? [block.text] : []))
    .join("\n");
}

// One thread, read back through the same endpoints a browser uses, turn by
// turn. `dispatch` is what the ingress handed the run as its input; `run` is
// what the run itself appended on top.
class ThreadRecorder {
  readonly turns: Turn[] = [];
  private seen = 0;

  constructor(
    private readonly page: Page,
    readonly threadId: string,
  ) {}

  private async messages(): Promise<StateMessage[]> {
    const res = await this.page.request.get(`/threads/${this.threadId}/state`);
    expect(res.ok(), await res.text()).toBeTruthy();
    const state = (await res.json()) as {
      values?: { messages?: StateMessage[] };
    };
    return state.values?.messages ?? [];
  }

  private async runs(): Promise<ThreadRun[]> {
    const res = await this.page.request.get(`/threads/${this.threadId}/runs`);
    expect(res.ok(), await res.text()).toBeTruthy();
    return (await res.json()) as ThreadRun[];
  }

  // Every run has to have finished before the next turn: the local harness
  // drops a follow-up posted while the previous run is still starting.
  async settle(expected: number) {
    await expect
      .poll(
        async () => {
          const runs = await this.runs();
          const statuses = runs.map((run) => run.status ?? "unknown");
          if (runs.length < expected)
            return `${runs.length} run(s): ${statuses.join(",")}`;
          if (
            statuses.some(
              (status) => status === "pending" || status === "running",
            )
          )
            return `still running: ${statuses.join(",")}`;
          return statuses.join(",");
        },
        { timeout: 60_000, intervals: [500] },
      )
      .toBe(Array.from({ length: expected }, () => "success").join(","));
  }

  async record(turn: Omit<Turn, "dispatch" | "run">) {
    const messages = await this.messages();
    const appended = messages.slice(this.seen);
    this.seen = messages.length;
    const dispatched = new Set(
      (await this.runs()).flatMap((run) =>
        (run.kwargs?.input?.messages ?? []).map((message) =>
          contentText(message),
        ),
      ),
    );
    const human = appended
      .filter((message) => message.type === "human")
      .map((message) => contentText(message));
    const recorded: Turn = {
      ...turn,
      dispatch: human.filter((content) => dispatched.has(content)),
      run: human.filter((content) => !dispatched.has(content)),
    };
    this.turns.push(recorded);
    return recorded;
  }
}

function renderDump(turns: Turn[]): string {
  const parts = [
    "# Thread context dump\n" +
      "Every message the model is handed, turn by turn, recorded by thread_context.spec.ts " +
      "driving the real server. Regenerate with UPDATE_THREAD_CONTEXT_FIXTURE=1.\n",
  ];
  for (const turn of turns) {
    parts.push(
      `## ${turn.title}\nGiven: ${turn.given}\nWhen: ${turn.when}\nThen: ${turn.outcome}\n`,
    );
    parts.push("### dispatch appends");
    if (turn.dispatch.length === 0)
      parts.push("(the message is refused; nothing is dispatched)\n");
    for (const content of turn.dispatch)
      parts.push(`\`\`\`xml\n${content}\n\`\`\`\n`);
    parts.push("### run appends");
    if (turn.run.length === 0)
      parts.push(
        turn.ran
          ? "(everyone here is already described; nothing re-sent)\n"
          : "(no run started)\n",
      );
    for (const content of turn.run)
      parts.push(`\`\`\`xml\n${content}\n\`\`\`\n`);
  }
  return parts.join("\n");
}

// Three things differ run to run and nothing else does: the `users` row id
// behind each person (a fresh UUIDv7 per database), the LangGraph thread id
// (derived from the Slack thread's wall-clock timestamp) and the Slack
// timestamps themselves. Each person is named from the block that introduces
// them, so the placeholder says who it is; the timestamps are numbered in the
// order the dump first shows them, so a repeated one stays recognisably the
// same message.
function normalize(dump: string, threadId: string): string {
  const named = new Map<string, string>();
  for (const [, id, name] of dump.matchAll(
    /<dynamic-context kind="person" id="(user:[0-9a-f-]+)">\ndisplay_name: (\S+)/g,
  )) {
    named.set(id, `user:<${name.toLowerCase()}>`);
  }
  let normalized = dump.split(threadId).join("<thread-id>");
  for (const [id, alias] of named)
    normalized = normalized.split(id).join(alias);
  const timestamps = new Map<string, string>();
  return normalized.replace(/\b\d{10}\.\d{6}\b/g, (timestamp) => {
    const alias =
      timestamps.get(timestamp) ?? `<slack-ts-${timestamps.size + 1}>`;
    timestamps.set(timestamp, alias);
    return alias;
  });
}

async function sendSlack(
  page: Page,
  data: Record<string, unknown>,
): Promise<SlackSend> {
  const res = await page.request.post("/mock/slack/send", { data });
  expect(res.ok(), await res.text()).toBeTruthy();
  const sent = (await res.json()) as SlackSend;
  expect(sent.webhook.status).toBe("accepted");
  return sent;
}

async function clearInstructions(page: Page) {
  const res = await page.request.delete("/dashboard/api/me/instructions", {
    headers: SAME_ORIGIN_HEADERS,
  });
  expect(res.ok(), await res.text()).toBeTruthy();
}

async function botTexts(page: Page): Promise<string[]> {
  const res = await page.request.get("/mock/slack/messages");
  expect(res.ok(), await res.text()).toBeTruthy();
  const messages = (await res.json()) as Array<{
    text: string;
    is_bot: boolean;
  }>;
  return messages
    .filter((message) => message.is_bot)
    .map((message) => message.text);
}

test("records every message the model is handed as people come and go", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const reset = await page.request.post("/control/reset");
  expect(reset.ok(), await reset.text()).toBeTruthy();

  // An earlier spec may have left standing instructions on either of them, and
  // the dump would record them. Nothing else about these two is written from
  // here: a personal setting saved for a test user outlives this run, and the
  // next spec would see it.
  await loginAs(page, OTHER_USER);
  await clearInstructions(page);
  await loginAs(page, SAME_USER);
  await clearInstructions(page);

  const opened = await sendSlack(page, {
    text: "<@U0BOT> add a greet() helper",
    user: ALICE_SLACK,
  });
  const thread = new ThreadRecorder(page, opened.thread_id);
  await thread.settle(1);

  const first = await thread.record({
    title: "Turn 1: Alice starts the thread from Slack",
    given:
      "an empty thread; Alice has a linked GitHub account and is a workspace admin",
    when: "she mentions the bot: add a greet() helper",
    outcome:
      "dispatch introduces the channel, carrying everything that stays true of the thread, then the run adds the one block that describes her",
    ran: true,
  });
  expect(first.dispatch.join("\n")).not.toContain(
    '<dynamic-context kind="person"',
  );
  // Everything constant about this Slack thread rides the channel block, so no
  // turn has to restate it.
  expect(first.dispatch[0]).toContain('kind="channel"');
  expect(first.dispatch[0]).toContain("name: #demo");
  expect(first.dispatch[0]).toContain("topic (untrusted): Demo channel topic");
  expect(first.dispatch[0]).toContain("default_repo: fakeorg/demo");
  expect(first.dispatch[0]).toContain("web_url: ");
  expect(first.run).toHaveLength(1);
  expect(first.run[0]).toContain("display_name: Alice");
  expect(first.run[0]).toContain(
    "commit_email: alice@users.noreply.github.com",
  );
  expect(first.run[0]).toContain("open_swe_account: linked");
  expect(first.run[0]).toContain("workspace_admin: yes");
  expect(first.run[0]).toContain("new_prs: as drafts");

  await sendSlack(page, {
    text: "<@U0BOT> also add a docstring",
    user: ALICE_SLACK,
    thread_ts: opened.thread_ts,
  });
  await thread.settle(2);
  const second = await thread.record({
    title: "Turn 2: Alice follows up",
    given: "turn 1 has run to completion",
    when: "Alice replies in the same Slack thread: also add a docstring",
    outcome:
      "her envelope alone — the channel is described, her turn-1 message and the bot's replies are already in the thread, and nothing about her has changed",
    ran: true,
  });
  expect(second.run).toHaveLength(0);
  // Only what is new: no channel block again, no replay of what the thread
  // already holds, and none of the bot's own Slack replies.
  expect(second.dispatch).toHaveLength(1);
  expect(second.dispatch[0]).toContain("also add a docstring");

  await sendSlack(page, {
    text: "<@U0BOT> make it return bytes",
    user: BOB_SLACK,
    thread_ts: opened.thread_ts,
  });
  await thread.settle(3);
  const third = await thread.record({
    title: "Turn 3: Bob joins",
    given: "Bob has a linked account too, but is not a workspace admin",
    when: "Bob replies: make it return bytes",
    outcome:
      "the run adds his block; Alice's is not re-sent, and dispatch does not describe her either",
    ran: true,
  });
  expect(third.dispatch).toHaveLength(1);
  expect(third.run).toHaveLength(1);
  expect(third.run[0]).toContain("display_name: Bob");
  expect(third.run[0]).toContain("workspace_admin: no");

  await loginAs(page, SAME_USER);
  await page.goto(`/agents/${thread.threadId}`);
  await typeIntoComposer(page, "ship it");
  await thread.settle(4);
  const fourth = await thread.record({
    title: "Turn 4: Alice switches to the web dashboard",
    given: "the thread has Alice and Bob",
    when: "Alice types in the dashboard: ship it",
    outcome:
      "the same user: id on a web envelope, and nothing else — what she is does not depend on where she typed",
    ran: true,
  });
  expect(fourth.run).toHaveLength(0);
  expect(fourth.dispatch.join("\n")).toContain('surface="web"');

  await loginAs(page, OTHER_USER);
  const saved = await page.request.put("/dashboard/api/me/instructions", {
    headers: SAME_ORIGIN_HEADERS,
    data: { instructions: BOB_INSTRUCTIONS },
  });
  expect(saved.ok(), await saved.text()).toBeTruthy();
  await sendSlack(page, {
    text: "<@U0BOT> open the PR",
    user: BOB_SLACK,
    thread_ts: opened.thread_ts,
  });
  await thread.settle(5);
  const fifth = await thread.record({
    title: "Turn 5: Bob sets standing instructions, then asks for the PR",
    given: "Bob saved personal instructions between turns",
    when: "Bob replies: open the PR",
    outcome: "only Bob's block is re-sent, now carrying his instructions",
    ran: true,
  });
  expect(fifth.run).toHaveLength(1);
  expect(fifth.run[0]).toContain("display_name: Bob");
  expect(fifth.run[0]).toContain("standing_instructions:");
  expect(fifth.run[0]).toContain("Never use ripgrep.");

  // Open SWE commits and opens pull requests as the person who asked, so a
  // Slack account it cannot resolve to a GitHub one never reaches the model at
  // all: the webhook answers in the Slack thread instead.
  await sendSlack(page, {
    text: "<@U0BOT> can it handle unicode?",
    user: CAROL_SLACK,
    thread_ts: opened.thread_ts,
  });
  await expect
    .poll(async () => (await botTexts(page)).join("\n"), {
      timeout: 30_000,
      intervals: [500],
    })
    .toContain("I couldn't resolve your GitHub account from Slack");
  const sixth = await thread.record({
    title: "Turn 6: Carol, with no Open SWE account, chimes in",
    given:
      "Carol never signed in to Open SWE, so nothing links her Slack account to a GitHub one",
    when: "she replies: can it handle unicode?",
    outcome:
      "no run starts and the thread is untouched; Slack gets an account-link prompt instead",
    ran: false,
  });
  expect(sixth.dispatch).toHaveLength(0);
  expect(sixth.run).toHaveLength(0);

  // Bob's instructions outlive this thread, and the next spec to describe him
  // to a model would carry them.
  await loginAs(page, OTHER_USER);
  await clearInstructions(page);

  // Nothing about the Slack surface itself is a message any more: the rules are
  // in the system prompt and the thread's own data is in the channel block.
  const everything = thread.turns
    .flatMap((turn) => [...turn.dispatch, ...turn.run])
    .join("\n");
  expect(everything).not.toContain("system:slack-context");
  expect(everything).not.toContain("system:open-swe");

  const dump = normalize(renderDump(thread.turns), thread.threadId);
  if (process.env.UPDATE_THREAD_CONTEXT_FIXTURE) writeFileSync(FIXTURE, dump);
  expect(
    dump,
    "thread_context.md is out of date; rerun with UPDATE_THREAD_CONTEXT_FIXTURE=1",
  ).toBe(readFileSync(FIXTURE, "utf8"));
});
