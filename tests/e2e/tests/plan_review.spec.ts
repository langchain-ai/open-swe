import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

// Full plan-review flow, driven through the mock Slack UI + the real dashboard:
//   user asks Open SWE in Slack to PLAN something ->
//   agent calls enter_plan_mode, posts the plan-review link to Slack, writes the
//   plan as a markdown file (save_plan), and posts "ready" back to Slack ->
//   owner (user1) and a collaborator (user2) both open the plan and leave inline
//   comments using BlockNote's native comment UI (synced live via Yjs) ->
//   only the owner can approve -> on approval the agent implements, opens a PR,
//   and replies in Slack with the link, having received the reviewers' comments.
// Only the LLM is faked; all agent + dashboard code runs for real.

const OWNER = { login: "alice", email: "alice@example.com" };
const COLLABORATOR = { login: "bob", email: "bob@example.com" };

async function botMessages(request: APIRequestContext): Promise<Array<string>> {
  const res = await request.get("/mock/slack/messages");
  const msgs = (await res.json()) as Array<{ text: string; is_bot: boolean }>;
  return msgs.filter((m) => m.is_bot).map((m) => m.text);
}

// Create an inline comment via BlockNote's native UI: select text in the plan,
// click "Add comment" in the formatting toolbar, type, and save.
async function addInlineComment(page: Page, anchorText: string, comment: string) {
  await page.locator(".bn-editor").getByText(anchorText, { exact: false }).first().selectText();
  await page.locator('[data-test="addcomment"]').click();
  await page.locator(".bn-comment-editor .ProseMirror").last().waitFor({ state: "visible" });
  await page.keyboard.type(comment);
  await page.locator('[data-test="save"]').click();
}

test.describe("Plan review (BlockNote native comments)", () => {
  test("Slack plan request → inline comments → owner approves → PR", async ({
    browser,
    request,
  }) => {
    // 1. A user asks the bot to PLAN something in Slack.
    await request.post("/control/reset");
    const send = await request.post("/mock/slack/send", {
      data: { text: "<@U0BOT> plan how to add a greet() helper", mention_bot: true },
    });
    const { thread_id: threadId } = (await send.json()) as { thread_id: string };
    expect(threadId).toBeTruthy();
    const planPath = `/agents/${threadId}/plan`;

    // 1a. enter_plan_mode must actually engage, not error out. Its Command must
    //     carry a terminating ToolMessage; without it the tool call fails and is
    //     swallowed into an error tool message while the agent silently proceeds
    //     as a normal run. Assert the tool's success message landed in the
    //     thread (it wouldn't if the call had errored).
    await expect
      .poll(
        async () => {
          const res = await request.get(`/threads/${threadId}/state`);
          const state = (await res.json()) as {
            values?: { messages?: Array<{ content?: unknown }> };
          };
          return (state.values?.messages ?? [])
            .map((m) =>
              typeof m.content === "string" ? m.content : JSON.stringify(m.content),
            )
            .some((c) => c.includes("Plan mode is active"));
        },
        { timeout: 60_000 },
      )
      .toBe(true);

    // 2. The agent shares the plan-review link, then announces the plan is ready.
    await expect
      .poll(async () => (await botMessages(request)).join("\n"), { timeout: 60_000 })
      .toMatch(/\/agents\/[^/]+\/plan\b/);
    await expect
      .poll(async () => (await botMessages(request)).join("\n"), { timeout: 60_000 })
      .toMatch(/ready for review/i);

    // 3. The OWNER opens the conversation, sees the "Review plan" banner, and
    //    follows it into the plan — which renders inside the agents shell.
    const ownerCtx = await browser.newContext();
    await ownerCtx.request.post("/control/login", { data: OWNER });
    const owner = await ownerCtx.newPage();
    await owner.goto(`/agents/${threadId}`);
    const reviewLink = owner.getByTestId("review-plan-link");
    await expect(reviewLink).toBeVisible({ timeout: 30_000 });
    await reviewLink.click();
    await expect(owner).toHaveURL(new RegExp(`/agents/${threadId}/plan$`));
    await expect(owner.getByTestId("plan-review")).toBeVisible({ timeout: 30_000 });
    // The agents sidebar is present (plan lives inside the shell).
    await expect(owner.getByText("Back to conversation")).toBeVisible();
    await expect(owner.getByTestId("plan-document")).toContainText("greet", {
      timeout: 30_000,
    });
    await expect(owner.getByTestId("approve-plan")).toBeVisible();

    // Owner leaves an inline comment on the plan heading (a comment wraps the
    // text in a `.bn-thread-mark`).
    await addInlineComment(owner, "Add greet() helper", "Owner: looks solid, ship it.");
    await expect(owner.locator(".bn-thread-mark")).toHaveCount(1);

    // 4. A COLLABORATOR opens the same plan: sees it AND the owner's comment
    //    (synced live via Yjs), but has NO approve button.
    const collabCtx = await browser.newContext();
    await collabCtx.request.post("/control/login", { data: COLLABORATOR });
    const collab = await collabCtx.newPage();
    await collab.goto(planPath);
    await expect(collab.getByTestId("plan-review")).toBeVisible({ timeout: 30_000 });
    await expect(collab.getByTestId("plan-document")).toContainText("greet", {
      timeout: 30_000,
    });
    await expect(collab.locator(".bn-thread-mark")).toHaveCount(1); // owner's comment, synced
    await expect(collab.getByTestId("approve-plan")).toHaveCount(0);
    await expect(collab.getByTestId("reject-plan")).toBeVisible();

    // Collaborator leaves inline feedback on a different part of the plan.
    await addInlineComment(
      collab,
      "tiny greeting helper",
      "Reviewer: please also add a docstring.",
    );
    await expect(collab.locator(".bn-thread-mark")).toHaveCount(2);

    // 5. The owner sees the collaborator's comment sync in, then approves.
    await expect(owner.locator(".bn-thread-mark")).toHaveCount(2);
    await owner.getByTestId("approve-plan").click();
    await expect(owner.getByTestId("plan-decision")).toContainText(/implementing/i);

    // 6. The agent implements, opens a PR, and links it back in the Slack thread,
    //    echoing the reviewers' feedback it received — which proves the
    //    collaborator's comment synced to the owner and was harvested on approve.
    await expect
      .poll(async () => (await botMessages(request)).join("\n"), { timeout: 90_000 })
      .toMatch(/\/pull\//);
    expect((await botMessages(request)).join("\n")).toMatch(/docstring/);

    const prs = (await (await request.get("/mock/github/data")).json()) as Array<unknown>;
    expect(prs.length).toBeGreaterThan(0);

    await ownerCtx.close();
    await collabCtx.close();
  });
});
