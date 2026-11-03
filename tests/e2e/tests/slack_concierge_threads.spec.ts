import { test, expect, type APIRequestContext } from "@playwright/test";

const BOB = { login: "bob", slack: "U_BOB" };
const DM = "D_BOB";

type SlackMessage = {
  text: string;
  is_bot: boolean;
  ts: string;
  thread_ts: string;
};

async function control(
  request: APIRequestContext,
  path: string,
  data: unknown,
): Promise<unknown> {
  const res = await request.post(path, { data });
  if (!res.ok()) {
    throw new Error(`POST ${path} → ${res.status()}: ${await res.text()}`);
  }
  return await res.json();
}

async function dmMessages(
  request: APIRequestContext,
): Promise<Array<SlackMessage>> {
  const res = await request.get(`/mock/slack/messages?channel=${DM}`);
  return (await res.json()) as Array<SlackMessage>;
}

test.describe("Slack concierge DM threads", () => {
  test.beforeEach(async ({ request }) => {
    await control(request, "/control/reset", {});
    await control(request, "/control/user-preferences", {
      login: BOB.login,
      preferences: { concierge_mode: true },
    });
  });

  test.afterEach(async ({ request }) => {
    await control(request, "/control/user-preferences", {
      login: BOB.login,
      preferences: { concierge_mode: false },
    });
  });

  test("asked to use threads, the bot answers a top-level DM in that message's thread", async ({
    request,
  }) => {
    const text = "Please reply in threads from now on E2E_CONCIERGE_THREADS";
    const sent = (await control(request, "/mock/slack/send", {
      channel: DM,
      channel_type: "im",
      user: BOB.slack,
      mention_bot: false,
      text,
    })) as { thread_ts: string };
    expect(sent.thread_ts, "Bob's DM should run in concierge mode").toBe("0");

    const asked = (await dmMessages(request)).find((m) => m.text === text);
    expect(asked, "Bob's message should be top-level").toBeTruthy();
    expect(asked!.thread_ts).toBe(asked!.ts);

    let reply: SlackMessage | undefined;
    await expect
      .poll(
        async () => {
          reply = (await dmMessages(request)).find(
            (m) => m.is_bot && m.text.includes("Threaded, as you asked."),
          );
          return reply !== undefined;
        },
        { message: "the bot should reply", timeout: 60_000 },
      )
      .toBe(true);
    expect(reply!.thread_ts, "the reply should sit in Bob's thread").toBe(
      asked!.ts,
    );
    expect(reply!.ts).not.toBe(asked!.ts);
  });
});
