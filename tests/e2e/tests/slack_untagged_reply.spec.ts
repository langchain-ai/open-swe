import { test, expect, type APIRequestContext } from "@playwright/test";

// Feature: in a Slack thread whose only participants are Open SWE and one human,
// a follow-up no longer needs to @-mention the bot — UNLESS it tags a different
// user. Driven through the real webhook + real agent; only the LLM is faked.

type SendResult = {
  thread_ts: string;
  thread_id: string;
  webhook: { status: string; reason?: string };
};

async function send(
  request: APIRequestContext,
  data: Record<string, unknown>,
): Promise<SendResult> {
  const res = await request.post("/mock/slack/send", { data });
  return (await res.json()) as SendResult;
}

async function botTexts(request: APIRequestContext): Promise<string[]> {
  const res = await request.get("/mock/slack/messages");
  const msgs = (await res.json()) as Array<{ text: string; is_bot: boolean }>;
  return msgs.filter((m) => m.is_bot).map((m) => m.text);
}

async function threadStatus(
  request: APIRequestContext,
  threadId: string,
): Promise<string> {
  const res = await request.get(`/threads/${threadId}`);
  if (!res.ok()) return "";
  const thread = (await res.json()) as { status?: string };
  return thread.status ?? "";
}

async function stateText(
  request: APIRequestContext,
  threadId: string,
): Promise<string> {
  const res = await request.get(`/threads/${threadId}/state`);
  const state = (await res.json()) as {
    values?: { messages?: Array<{ content?: unknown }> };
  };
  return (state.values?.messages ?? [])
    .map((m) =>
      typeof m.content === "string" ? m.content : JSON.stringify(m.content),
    )
    .join("\n");
}

// Open a two-party thread: Alice @-mentions the bot, the agent implements and
// replies, so the thread now holds exactly Alice + Open SWE.
async function openTwoPartyThread(
  request: APIRequestContext,
): Promise<SendResult> {
  await request.post("/control/reset");
  const opened = await send(request, {
    text: "<@U0BOT> please add a greet() helper and open a PR",
    mention_bot: true,
  });
  expect(opened.webhook.status).toBe("accepted");
  await expect
    .poll(async () => (await botTexts(request)).join("\n"), { timeout: 60_000 })
    .toContain("/pull/");
  // Let the opening run settle so a follow-up dispatches immediately rather than
  // queueing behind an about-to-finish run.
  await expect
    .poll(() => threadStatus(request, opened.thread_id), { timeout: 30_000 })
    .not.toBe("busy");
  return opened;
}

test.describe("Slack untagged two-party replies", () => {
  test("an untagged follow-up triggers a run once Open SWE is in the thread", async ({
    request,
  }) => {
    const { thread_ts, thread_id } = await openTwoPartyThread(request);

    // A plain message (no @-mention) in the two-party thread is accepted…
    const followUp = await send(request, {
      text: "actually, can you also add a docstring?",
      mention_bot: false,
      thread_ts,
    });
    expect(followUp.webhook.status).toBe("accepted");

    // …and the agent actually runs on it (its follow-up reply lands in state).
    await expect
      .poll(async () => stateText(request, thread_id), { timeout: 60_000 })
      .toContain("anything else you'd like changed");
  });

  test("an untagged message tagging another user is ignored", async ({
    request,
  }) => {
    const { thread_ts } = await openTwoPartyThread(request);

    // Tagging Bob (a different, non-bot user) hands the turn to him, not the agent.
    const toSomeoneElse = await send(request, {
      text: "<@U_BOB> could you take a look at this one?",
      mention_bot: false,
      thread_ts,
    });
    expect(toSomeoneElse.webhook.status).toBe("ignored");
  });

  test("an untagged message in a brand-new thread is ignored", async ({
    request,
  }) => {
    await request.post("/control/reset");
    // No prior Open SWE participation → the bot must be @-mentioned to engage.
    const fresh = await send(request, {
      text: "just chatting with the team, nothing for the bot",
      mention_bot: false,
    });
    expect(fresh.webhook.status).toBe("ignored");
  });
});
