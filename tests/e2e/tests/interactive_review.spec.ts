import { test, expect, type Page } from "@playwright/test";
import {
  SAME_ORIGIN_HEADERS,
  SAME_USER,
  loginAs,
  seedOpenPullRequest,
} from "./helpers/dashboard";

// An interactive review runs in the conversation that asked for it: a Slack
// thread, or the dashboard. The tool, the guide's middleware, its server-side
// "Next" and the closing button all run the real code; the pull request and its
// one-chunk plan are seeded, and only the model is scripted.

const OWNER = "fakeorg";
const REPO = "demo";
const GREETING = "I'll walk you through this pull request one chunk at a time.";
const CHUNK_TITLE = "Add the farewell helper";

type SlackMessage = {
  text: string;
  is_bot: boolean;
  ts: string;
  blocks: Array<{
    type: string;
    elements?: Array<{ text?: { text: string } | string }>;
  }> | null;
};

// Someone else's pull request, so the reader ends by approving it.
async function seedReview(page: Page): Promise<string> {
  await page.request.post("/control/reset");
  const pr = await seedOpenPullRequest(page, {
    repo: `${OWNER}/${REPO}`,
    head: "interactive-review",
    title: "Add a farewell helper",
    author: "bob",
    real_head: true,
    files: {
      "farewell.py": 'def farewell(name):\n    return f"Bye, {name}!"\n',
    },
  });
  const plan = await page.request.post("/control/walkthrough", {
    data: {
      repo: `${OWNER}/${REPO}`,
      number: pr.number,
      title: CHUNK_TITLE,
      explanation: "Adds `farewell()`, the counterpart to `greet()`.",
      code: true,
    },
  });
  expect(plan.ok(), await plan.text()).toBeTruthy();
  return `https://github.com/${OWNER}/${REPO}/pull/${pr.number}`;
}

function buttons(message: SlackMessage): Array<string> {
  return (message.blocks ?? [])
    .filter((block) => block.type === "actions")
    .flatMap((block) => block.elements ?? [])
    .map((element) =>
      typeof element.text === "string"
        ? element.text
        : (element.text?.text ?? ""),
    );
}

test.describe("Interactive review", () => {
  test("runs in the Slack thread that asked for it", async ({ page }) => {
    const prUrl = await seedReview(page);
    const sent = await page.request.post("/mock/slack/send", {
      data: {
        text: `<@U0BOT> walk me through ${prUrl} E2E_INTERACTIVE_REVIEW`,
        mention_bot: true,
      },
    });
    expect(sent.ok(), await sent.text()).toBeTruthy();
    const { channel, thread_ts: threadTs } = (await (
      await page.request.get("/control/state")
    ).json()) as { channel: string; thread_ts: string };
    const messages = async () =>
      (
        (await (
          await page.request.get(
            `/mock/slack/messages?channel=${channel}&thread_ts=${threadTs}`,
          )
        ).json()) as Array<SlackMessage>
      ).filter((message) => message.is_bot);

    await expect
      .poll(async () => (await messages()).map((m) => m.text).join("\n"), {
        timeout: 90_000,
      })
      .toContain(GREETING);
    await expect
      .poll(
        async () =>
          (await messages()).find((m) => buttons(m).includes("Next"))?.text,
        { timeout: 60_000 },
      )
      .toContain(CHUNK_TITLE);

    await page.goto("/mock/slack");
    await expect(page.locator("#thread")).toContainText(
      "Interactive review in progress",
    );
    await expect(page.locator("#thread")).toContainText(CHUNK_TITLE);
    await page.screenshot({
      path: "screenshots/interactive-review-slack.png",
      fullPage: true,
    });
    await page.locator("#thread .actions button", { hasText: "Next" }).click();

    await expect
      .poll(
        async () =>
          (await messages()).some((m) =>
            buttons(m).includes("Approve on GitHub"),
          ),
        { timeout: 90_000 },
      )
      .toBe(true);
    const chunk = (await messages()).find((m) => m.text.includes(CHUNK_TITLE));
    expect(chunk && buttons(chunk)).toEqual([]);
    await expect(page.locator("#thread")).toContainText(
      "Interactive review finished",
    );
  });

  test("runs in the dashboard conversation that asked for it", async ({
    page,
  }) => {
    const prUrl = await seedReview(page);
    await loginAs(page, SAME_USER);
    const created = await page.request.post("/dashboard/api/threads", {
      headers: SAME_ORIGIN_HEADERS,
      data: {
        prompt: `Walk me through ${prUrl} E2E_INTERACTIVE_REVIEW_WEB`,
        repo: `${OWNER}/${REPO}`,
      },
    });
    expect(created.ok(), await created.text()).toBeTruthy();
    const { thread_id: threadId } = (await created.json()) as {
      thread_id: string;
    };

    await page.goto(`/agents/${threadId}`);
    const cards = page.getByTestId("interactive-review-card");
    const chunk = cards.filter({ hasText: CHUNK_TITLE });
    await expect(chunk).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText(GREETING)).toBeVisible();
    await page.screenshot({
      path: "screenshots/interactive-review-web.png",
      fullPage: true,
    });

    await chunk.getByRole("button", { name: "Next" }).click();
    await expect(chunk.getByRole("button", { name: "✓ Seen" })).toBeDisabled();
    await expect(
      cards.getByRole("button", { name: "Approve on GitHub" }),
    ).toBeVisible({ timeout: 90_000 });
    await page.screenshot({
      path: "screenshots/interactive-review-web-finished.png",
      fullPage: true,
    });
  });
});
