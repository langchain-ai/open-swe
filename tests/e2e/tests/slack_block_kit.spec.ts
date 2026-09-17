import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

// Block Kit interactions, end to end, through the real interactivity route:
//
//   a user asks in mock Slack ->
//   the agent replies with a card: a button, a link button, and a select ->
//   the link button is a plain anchor and sends nothing ->
//   clicking the button resumes the agent's own thread, which reports back the
//   `action_id` it chose and what was picked ->
//   the spent button is replaced in the message, and a second click on it is
//   refused rather than resuming the thread twice ->
//   choosing from the select resumes the thread again, because naming a value
//   is not spending an answer.
//
// Nothing here calls the continuation code directly: every click is posted to
// /webhooks/slack/interactivity the way Slack would post it.

const CARD = /unit tests\s*failed on this branch/i;

async function botTexts(request: APIRequestContext): Promise<Array<string>> {
  const res = await request.get("/mock/slack/messages");
  const msgs = (await res.json()) as Array<{ text: string; is_bot: boolean }>;
  return msgs.filter((m) => m.is_bot).map((m) => m.text);
}

/** Every `E2E_CONTINUATION_OK …` line the agent has reported so far. */
async function reported(request: APIRequestContext): Promise<Array<string>> {
  return (await botTexts(request)).filter((text) =>
    text.includes("E2E_CONTINUATION_OK"),
  );
}

function card(page: Page) {
  return page.locator(".msg.bot").filter({ hasText: CARD });
}

test.describe("Slack Block Kit interactions", () => {
  test("a click resumes the thread, once, and a select can be reused", async ({
    page,
    request,
  }) => {
    test.setTimeout(300_000);
    await request.post("/control/reset");

    const send = await request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> the tests are red, give me the options E2E_BLOCK_KIT",
        mention_bot: true,
      },
    });
    expect(send.ok()).toBeTruthy();

    // 1. The card arrives with all three elements rendered.
    await page.goto("/mock/slack");
    await expect(card(page)).toBeVisible({ timeout: 120_000 });
    const rerun = card(page).getByRole("button", { name: "Rerun tests" });
    await expect(rerun).toBeVisible();

    // A link button is an anchor Slack opens itself, never an interaction.
    const link = card(page).getByRole("link", { name: "Open the run" });
    await expect(link).toHaveAttribute(
      "href",
      "https://ci.example.com/runs/9001",
    );
    expect(await reported(request)).toHaveLength(0);

    // 2. Clicking the button resumes the thread, which names the agent's own
    //    action_id back — proof the token was translated, not leaked.
    await rerun.click();
    await expect
      .poll(async () => (await reported(request)).length, { timeout: 120_000 })
      .toBe(1);
    expect((await reported(request))[0]).toContain("rerun_tests");
    expect((await reported(request))[0]).toContain("Rerun tests");

    // 3. The spent button is gone from the message, replaced by what was done.
    await expect(card(page).getByText(/Rerun tests/)).toBeVisible();
    await expect(
      card(page).getByRole("button", { name: "Rerun tests" }),
    ).toHaveCount(0);

    // 4. The select is still usable: naming a value spends no answer.
    await card(page).locator("select").selectOption("develop");
    await expect
      .poll(async () => (await reported(request)).length, { timeout: 120_000 })
      .toBe(2);
    expect((await reported(request))[1]).toContain("pick_base");
    expect((await reported(request))[1]).toContain("develop");
  });

  test("a replayed click is refused instead of resuming twice", async ({
    request,
  }) => {
    test.setTimeout(300_000);
    await request.post("/control/reset");

    const send = await request.post("/mock/slack/send", {
      data: {
        text: "<@U0BOT> options again please E2E_BLOCK_KIT",
        mention_bot: true,
      },
    });
    expect(send.ok()).toBeTruthy();

    // Wait for the card, then read the token Slack would send back.
    const button = await new Promise<{ action_id: string; message_ts: string }>(
      (resolve, reject) => {
        const deadline = Date.now() + 120_000;
        const look = async () => {
          const res = await request.get("/mock/slack/messages");
          const msgs = (await res.json()) as Array<{
            text: string;
            ts: string;
            is_bot: boolean;
            blocks?: Array<{
              type: string;
              elements?: Array<Record<string, string>>;
            }>;
          }>;
          for (const msg of msgs) {
            const actions = (msg.blocks || []).find(
              (b) => b.type === "actions",
            );
            const element = (actions?.elements || []).find(
              (e) => e.type === "button" && !e.url,
            );
            if (msg.is_bot && element) {
              resolve({ action_id: element.action_id, message_ts: msg.ts });
              return;
            }
          }
          if (Date.now() > deadline) {
            reject(new Error("the agent never posted an interactive card"));
            return;
          }
          setTimeout(look, 1_000);
        };
        void look();
      },
    );

    const click = () =>
      request.post("/mock/slack/action", {
        data: {
          action: {
            action_id: button.action_id,
            type: "button",
            text: { type: "plain_text", text: "Rerun tests" },
          },
          message_ts: button.message_ts,
          user: "U_ALICE",
        },
      });

    const first = await click();
    expect((await first.json()).status).toBe("accepted");

    // The same delivery again: the row is already claimed, so nothing resumes.
    const second = await click();
    expect(await second.json()).toEqual({
      status: "ignored",
      reason: "Slack continuation is no longer open",
    });

    await expect
      .poll(async () => (await reported(request)).length, { timeout: 120_000 })
      .toBe(1);
  });
});
