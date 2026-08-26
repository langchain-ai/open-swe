import { test, expect, type APIRequestContext } from "@playwright/test";

// Feature: with a firehose channel configured, every agent thread is duplicated
// into it — the inbound request, the agent's own prose, and one rolling task
// card standing in for its tool calls. Driven through the real webhook, the real
// agent and the real Slack client; only the LLM and the Slack API are faked.

const FIREHOSE = "C_FIREHOSE";

type SlackMessage = {
  text: string;
  is_bot: boolean;
  ts: string;
  thread_ts: string;
  blocks: Array<Record<string, unknown>> | null;
};

async function firehose(request: APIRequestContext): Promise<SlackMessage[]> {
  const res = await request.get(`/mock/slack/messages?channel=${FIREHOSE}`);
  return (await res.json()) as SlackMessage[];
}

function blockTypes(message: SlackMessage): string[] {
  return (message.blocks ?? []).map((block) => String(block.type));
}

function rendered(message: SlackMessage): string {
  return JSON.stringify(message.blocks ?? []);
}

test.describe("Slack firehose channel", () => {
  test.afterAll(async ({ request }) => {
    await request.post("/control/firehose-channel", {
      data: { enabled: false },
    });
  });

  test("mirrors a whole agent thread into the firehose channel", async ({
    request,
  }) => {
    await request.post("/control/reset");
    await request.post("/control/firehose-channel", { data: { enabled: true } });

    const res = await request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> please add a greet() helper and open a PR",
        mention_bot: true,
      },
    });
    const { thread_ts: sourceThreadTs } = (await res.json()) as {
      thread_ts: string;
    };

    // The run is done once its PR link lands back in the source channel.
    await expect
      .poll(
        async () => {
          const msgs = await request.get(
            `/mock/slack/messages?channel=C_DEMO&thread_ts=${sourceThreadTs}`,
          );
          return JSON.stringify(await msgs.json());
        },
        { timeout: 60_000 },
      )
      .toContain("/pull/");

    await expect
      .poll(async () => (await firehose(request)).length, { timeout: 60_000 })
      .toBeGreaterThan(3);

    const messages = await firehose(request);
    const root = messages[0];
    const replies = messages.slice(1);

    // A root message per agent thread, linking back to the web UI.
    expect(root.ts).toBe(root.thread_ts);
    expect(blockTypes(root)).toEqual(["markdown", "context"]);
    expect(rendered(root)).toContain("/agents/");
    expect(replies.every((m) => m.thread_ts === root.ts)).toBe(true);

    // The request that started the thread, then the agent's own prose.
    expect(rendered(replies[0])).toContain("greet() helper");
    expect(rendered(replies[1])).toContain(
      "Acknowledging the Slack request before starting work.",
    );

    // Tool calls collapse into task cards rather than one message apiece.
    const cards = replies.filter((m) => blockTypes(m).includes("task_card"));
    expect(cards.length).toBeGreaterThan(0);
    expect(cards.length).toBeLessThan(replies.length);
    expect(rendered(cards[0])).toContain("slack_thread_reply");

    // Every card settles once the run stops.
    for (const card of cards) {
      const [block] = card.blocks as Array<{ status: string }>;
      expect(block.status).toBe("complete");
    }
  });

  test("stays out of Slack entirely when no channel is configured", async ({
    request,
  }) => {
    await request.post("/control/reset");
    await request.post("/control/firehose-channel", {
      data: { enabled: false },
    });

    const res = await request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> please add a greet() helper and open a PR",
        mention_bot: true,
      },
    });
    const { thread_ts: sourceThreadTs } = (await res.json()) as {
      thread_ts: string;
    };
    await expect
      .poll(
        async () => {
          const msgs = await request.get(
            `/mock/slack/messages?channel=C_DEMO&thread_ts=${sourceThreadTs}`,
          );
          return JSON.stringify(await msgs.json());
        },
        { timeout: 60_000 },
      )
      .toContain("/pull/");

    expect(await firehose(request)).toEqual([]);
  });
});
