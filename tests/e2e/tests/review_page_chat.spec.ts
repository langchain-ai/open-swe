import { test, expect, type Locator, type Page } from "@playwright/test";
import {
  SAME_ORIGIN_HEADERS,
  SAME_USER,
  loginAs,
  seedOpenPullRequest,
  type SeededPullRequest,
} from "./helpers/dashboard";

const OWNER = "fakeorg";
const REPO = "demo";
const DESCRIPTION = "Adds the ALPHA and ZETA constant modules.";

function constants(prefix: string, count: number): string {
  return (
    Array.from(
      { length: count },
      (_, index) => `${prefix}_${index + 1} = ${index + 1}`,
    ).join("\n") + "\n"
  );
}

// alpha.py is long enough that zeta.py starts below the fold, so a chat
// pointing into zeta.py has to scroll the diff to be seen.
const FILES = {
  "alpha.py": constants("ALPHA", 150),
  "zeta.py": constants("ZETA", 60),
};

interface ReviewPayload {
  walkthrough: object | null;
  walkthrough_scout_thread_id: string | null;
}

interface FakeReviewComment {
  body: string;
  path: string;
  line: number;
  side: string;
  user: { login: string };
  pull_request_review_id: number;
}

interface FakeSubmittedReview {
  id: number;
  author: string;
  state: string;
  body: string;
}

interface FakePull {
  number: number;
  repo: string;
  review_comments: FakeReviewComment[];
  reviews: FakeSubmittedReview[];
  standalone_comment_posts: object[];
  issue_comments: Array<{ body: string }>;
}

function reviewApi(pr: SeededPullRequest, suffix = ""): string {
  return `/dashboard/api/reviews/${OWNER}/${REPO}/${pr.number}${suffix}`;
}

async function fakePull(page: Page, pr: SeededPullRequest): Promise<FakePull> {
  const res = await page.request.get("/mock/github/data");
  expect(res.ok()).toBeTruthy();
  const pulls = (await res.json()) as FakePull[];
  const pull = pulls.find(
    (item) => item.repo === `${OWNER}/${REPO}` && item.number === pr.number,
  );
  expect(pull, `no fake PR #${pr.number}`).toBeDefined();
  return pull as FakePull;
}

// Pull request numbers restart at 1 after a reset, and the chat and scout
// threads are keyed by the PR, so an earlier run's history would otherwise
// load into this one.
async function forgetReviewThreads(page: Page, pr: SeededPullRequest) {
  const chat = await page.request.get(reviewApi(pr, "/chat"));
  expect(chat.ok(), await chat.text()).toBeTruthy();
  const review = await page.request.get(reviewApi(pr));
  expect(review.ok(), await review.text()).toBeTruthy();
  const ids = [
    ((await chat.json()) as { thread_id: string }).thread_id,
    ((await review.json()) as ReviewPayload).walkthrough_scout_thread_id,
  ];
  for (const id of ids) {
    if (!id) continue;
    const res = await page.request.delete(`/threads/${id}`);
    expect([200, 204, 404], await res.text()).toContain(res.status());
  }
}

async function openReview(page: Page, pr: SeededPullRequest) {
  await page.goto(`/agents/reviews/${OWNER}/${REPO}/${pr.number}`);
  await expect(
    page.getByRole("heading", { name: "Changes", exact: true }),
  ).toBeVisible();
}

function chatPanel(page: Page): Locator {
  return page
    .locator("aside")
    .filter({ has: page.getByPlaceholder("Ask anything about this PR…") });
}

// The app shell's <main> wraps the side panel too; the diff column is the inner one.
function diffColumn(page: Page): Locator {
  return page
    .locator("main")
    .filter({ has: page.getByRole("heading", { name: "Changes" }) })
    .filter({ hasNot: page.getByPlaceholder("Ask anything about this PR…") });
}

async function openChatTab(page: Page) {
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await expect(
    page.getByPlaceholder("Ask anything about this PR…"),
  ).toBeVisible();
}

async function sendChat(page: Page, text: string) {
  const input = page.getByPlaceholder("Ask anything about this PR…");
  await input.fill(text);
  await input.press("Enter");
}

let pr: SeededPullRequest;

test.describe("review page", () => {
  test.beforeEach(async ({ page }) => {
    await page.request.post("/control/reset");
    await loginAs(page, SAME_USER);
    pr = await seedOpenPullRequest(page, {
      repo: `${OWNER}/${REPO}`,
      title: "Add constant modules",
      body: DESCRIPTION,
      author: "bob",
      head: "add-constants",
      files: FILES,
    });
    await forgetReviewThreads(page, pr);
  });

  test("opens a review from a pasted or typed pull request link", async ({
    page,
  }) => {
    const target = new RegExp(`/agents/reviews/${OWNER}/${REPO}/${pr.number}$`);
    const link = `https://github.com/${OWNER}/${REPO}/pull/${pr.number}/files?w=1`;

    await page.goto("/agents/reviews");
    const input = page.getByLabel("Open a pull request review");
    await expect(input).toBeVisible();
    await input.fill(`see ${link} thx`);
    await input.press("Enter");
    await expect(page).toHaveURL(target);

    await page.goto("/agents/reviews");
    await expect(input).toBeVisible();
    await input.focus();
    await input.evaluate((element, text) => {
      const data = new DataTransfer();
      data.setData("text/plain", text);
      element.dispatchEvent(
        new ClipboardEvent("paste", {
          clipboardData: data,
          bubbles: true,
          cancelable: true,
        }),
      );
    }, `see ${link} thx`);
    await expect(page).toHaveURL(target);
  });

  test("puts the state pill on the title line and links the title to GitHub", async ({
    page,
  }) => {
    await openReview(page, pr);
    const title = page
      .locator("h1")
      .filter({ has: page.locator('svg[aria-label="GitHub"]') });
    const link = title.getByRole("link");
    await expect(link).toContainText("Add constant modules");
    await expect(link).toContainText(`#${pr.number}`);
    await expect(link.locator('svg[aria-label="GitHub"]')).toBeVisible();

    const pill = title.locator("xpath=preceding-sibling::span[1]");
    await expect(pill).toHaveText("open");
    const pillBox = await pill.boundingBox();
    const titleBox = await title.boundingBox();
    expect(pillBox).not.toBeNull();
    expect(titleBox).not.toBeNull();
    const pillMiddle = pillBox!.y + pillBox!.height / 2;
    expect(pillMiddle).toBeGreaterThan(titleBox!.y);
    expect(pillMiddle).toBeLessThan(titleBox!.y + titleBox!.height);
    expect(pillBox!.x + pillBox!.width).toBeLessThanOrEqual(titleBox!.x);
  });

  test("renders author guidance between the description and the changes", async ({
    page,
  }) => {
    const seeded = await page.request.post("/control/guidance", {
      data: {
        repo: `${OWNER}/${REPO}`,
        number: pr.number,
        points: [
          {
            summary: "Keep the constants in flat modules",
            quote: "please don't nest these in a package",
            author: "bob",
          },
        ],
      },
    });
    expect(seeded.ok(), await seeded.text()).toBeTruthy();

    await openReview(page, pr);
    const guidance = page.getByRole("region", {
      name: "Human input",
    });
    await expect(guidance).toContainText("Keep the constants in flat modules");

    const description = await page.getByText(DESCRIPTION).boundingBox();
    const card = await guidance.boundingBox();
    const changes = await page
      .getByRole("heading", { name: "Changes", exact: true })
      .boundingBox();
    expect(description && card && changes).toBeTruthy();
    expect(description!.y + description!.height).toBeLessThanOrEqual(card!.y);
    expect(card!.y + card!.height).toBeLessThanOrEqual(changes!.y);
  });

  test("dismissing a walkthrough reports whether there was one", async ({
    page,
  }) => {
    const seeded = await page.request.post("/control/walkthrough", {
      data: { repo: `${OWNER}/${REPO}`, number: pr.number },
    });
    expect(seeded.ok(), await seeded.text()).toBeTruthy();
    const before = (await (
      await page.request.get(reviewApi(pr))
    ).json()) as ReviewPayload;
    expect(before.walkthrough).not.toBeNull();

    const first = await page.request.delete(reviewApi(pr, "/walkthrough"), {
      headers: SAME_ORIGIN_HEADERS,
    });
    expect(first.ok(), await first.text()).toBeTruthy();
    expect(await first.json()).toEqual({ dismissed: true });
    const after = (await (
      await page.request.get(reviewApi(pr))
    ).json()) as ReviewPayload;
    expect(after.walkthrough).toBeNull();

    const second = await page.request.delete(reviewApi(pr, "/walkthrough"), {
      headers: SAME_ORIGIN_HEADERS,
    });
    expect(second.ok(), await second.text()).toBeTruthy();
    expect(await second.json()).toEqual({ dismissed: false });
  });

  test("shows the scout's error when the walkthrough fails to build", async ({
    page,
  }) => {
    await openReview(page, pr);
    await expect(page.getByText("Read this PR step by step")).toBeVisible();
    const build = page.getByRole("button", { name: "Build walkthrough" });
    await expect(build).toBeEnabled();

    // Every label the button shows from the click to the run's end, so a
    // one-frame fall back to idle between the request and the poll is caught.
    await build.evaluate((button) => {
      const labels: string[] = [button.textContent ?? ""];
      const callout = button.parentElement!;
      new MutationObserver(() => {
        const label = callout.querySelector("button")?.textContent ?? "";
        if (labels[labels.length - 1] !== label) labels.push(label);
      }).observe(callout, {
        subtree: true,
        childList: true,
        characterData: true,
      });
      (window as unknown as { buildLabels: string[] }).buildLabels = labels;
    });

    await build.click();
    await expect(
      page.getByRole("button", { name: "Building…" }),
    ).toBeDisabled();
    await expect(page.getByText("Building the walkthrough…")).toBeVisible();

    const failure =
      "RuntimeError: E2E has no sandbox provider for the review scout";
    await expect(page.getByText(`Last attempt failed: ${failure}`)).toBeVisible(
      {
        timeout: 30_000,
      },
    );
    await expect(
      page.getByText("The walkthrough failed to build"),
    ).toBeVisible();
    await expect(
      page.getByText(`The review scout crashed: ${failure}`),
    ).toBeVisible();
    await expect(build).toBeEnabled();

    const labels = await page.evaluate(
      () => (window as unknown as { buildLabels: string[] }).buildLabels,
    );
    expect(labels).toEqual([
      "Build walkthrough",
      "Building…",
      "Build walkthrough",
    ]);
  });

  test("greets an unreviewed PR without claiming to have reviewed it", async ({
    page,
  }) => {
    await openReview(page, pr);
    await openChatTab(page);
    await expect(
      chatPanel(page).getByText("This PR hasn't been reviewed yet", {
        exact: false,
      }),
    ).toBeVisible();
    await expect(page.getByText("I've reviewed this PR")).toHaveCount(0);
    await expect(
      chatPanel(page).getByRole("button", {
        name: "Walk me through the review findings",
      }),
    ).toHaveCount(0);
  });

  test("chat-drafted comments join one pending review that submits together", async ({
    page,
  }) => {
    await openReview(page, pr);
    await openChatTab(page);
    await sendChat(page, "E2E_REVIEW_CHAT_COMMENTS leave comments on zeta.py");

    const chatCard = (location: string) =>
      chatPanel(page)
        .getByTestId("proposed-comment")
        .filter({ hasText: location });
    const inlineCard = (location: string) =>
      diffColumn(page)
        .getByTestId("proposed-comment")
        .filter({ hasText: location });
    const toPost = "zeta.py:R5";
    const toDiscard = "zeta.py:R12";

    await expect(chatCard(toPost)).toContainText("Draft review comment", {
      timeout: 30_000,
    });
    await expect(chatCard(toDiscard)).toContainText("Draft review comment");
    await expect(inlineCard(toPost)).toContainText("Draft review comment");
    await expect(inlineCard(toDiscard)).toContainText("Draft review comment");
    await expect(inlineCard(toPost).getByLabel("Comment body")).toHaveValue(
      "Rename ZETA_5 to something descriptive.",
    );

    const edited = "Rename ZETA_5 to GREETING_PREFIX.";
    await chatCard(toPost).getByLabel("Comment body").fill(edited);
    await expect(inlineCard(toPost).getByLabel("Comment body")).toHaveValue(
      edited,
    );
    // The diff renders only the lines near the viewport; the card's location
    // link scrolls the diff to its line.
    await chatCard(toDiscard).getByRole("button", { name: toDiscard }).click();
    await expect(inlineCard(toDiscard)).toBeInViewport();
    await inlineCard(toDiscard)
      .getByLabel("Comment body")
      .fill("Edited inline before discarding.");
    await expect(chatCard(toDiscard).getByLabel("Comment body")).toHaveValue(
      "Edited inline before discarding.",
    );

    const added = page.waitForResponse(
      (response) =>
        response.url().endsWith(reviewApi(pr, "/pending-review/comments")) &&
        response.request().method() === "POST",
    );
    await chatCard(toPost).getByRole("button", { name: toPost }).click();
    await expect(inlineCard(toPost)).toBeInViewport();
    await inlineCard(toPost)
      .getByRole("button", { name: "Add to review" })
      .click();
    const response = await added;
    expect(response.ok(), await response.text()).toBeTruthy();
    await expect(chatCard(toPost)).toContainText("Added to your review");
    await expect(inlineCard(toPost)).toHaveCount(0);

    const pendingCard = diffColumn(page)
      .getByTestId("pending-review-comment")
      .filter({ hasText: edited });
    await expect(pendingCard).toContainText("Pending");
    const reviewButton = page.getByRole("button", { name: /Review changes/ });
    await expect(reviewButton).toContainText("1");

    await chatCard(toDiscard).getByRole("button", { name: "Discard" }).click();
    await expect(chatCard(toDiscard)).toContainText("Comment discarded");
    await expect(inlineCard(toDiscard)).toHaveCount(0);

    // Nothing is visible to anyone else until the review is submitted.
    let fake = await fakePull(page, pr);
    const [pending] = fake.reviews;
    expect(fake.reviews).toHaveLength(1);
    expect(pending).toMatchObject({
      state: "PENDING",
      author: SAME_USER.login,
    });

    const final = "Rename ZETA_5 to GREETING_PREFIX, please.";
    await pendingCard.getByRole("button", { name: "Edit" }).click();
    await diffColumn(page).getByLabel("Pending comment body").fill(final);
    await diffColumn(page).getByRole("button", { name: "Save" }).click();
    await expect(
      diffColumn(page)
        .getByTestId("pending-review-comment")
        .filter({ hasText: final }),
    ).toBeVisible();

    await reviewButton.click();
    await expect(
      page.getByText("1 pending comment will be submitted with this review."),
    ).toBeVisible();
    await page.getByRole("radio", { name: /Request changes/ }).check();
    const summary = "A couple of naming nits.";
    await page.getByLabel("Review summary").fill(summary);
    const submitted = page.waitForResponse(
      (res) =>
        res.url().endsWith(reviewApi(pr, "/submit-review")) &&
        res.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Submit review" }).click();
    const submitResponse = await submitted;
    expect(submitResponse.ok(), await submitResponse.text()).toBeTruthy();
    await expect(
      diffColumn(page).getByTestId("pending-review-comment"),
    ).toHaveCount(0);

    fake = await fakePull(page, pr);
    expect(fake.reviews).toHaveLength(1);
    expect(fake.reviews[0]).toMatchObject({
      id: pending.id,
      state: "CHANGES_REQUESTED",
      body: summary,
      author: SAME_USER.login,
    });
    const comments = fake.review_comments.filter(
      (comment) => comment.pull_request_review_id === pending.id,
    );
    expect(comments).toHaveLength(1);
    expect(comments[0]).toMatchObject({
      body: final,
      path: "zeta.py",
      line: 5,
      side: "RIGHT",
      user: { login: SAME_USER.login },
    });
    expect(fake.standalone_comment_posts).toEqual([]);
  });

  test("discarding the pending review drops its comments", async ({ page }) => {
    await openReview(page, pr);
    await openChatTab(page);
    await sendChat(page, "E2E_REVIEW_CHAT_COMMENTS leave comments on zeta.py");
    const card = chatPanel(page)
      .getByTestId("proposed-comment")
      .filter({ hasText: "zeta.py:R5" });
    await expect(card).toContainText("Draft review comment", {
      timeout: 30_000,
    });
    await card.getByRole("button", { name: "Add to review" }).click();
    await expect(card).toContainText("Added to your review");
    const reviewButton = page.getByRole("button", { name: /Review changes/ });
    await expect(reviewButton).toContainText("1");

    await reviewButton.click();
    await page.getByRole("button", { name: "Discard review" }).click();
    await expect(reviewButton).not.toContainText("1");
    await expect(
      diffColumn(page).getByTestId("pending-review-comment"),
    ).toHaveCount(0);

    const fake = await fakePull(page, pr);
    expect(fake.reviews).toEqual([]);
    expect(fake.review_comments).toEqual([]);
    expect(fake.standalone_comment_posts).toEqual([]);
  });

  test("shows the conversation timeline and posts a top-level comment", async ({
    page,
  }) => {
    await openReview(page, pr);
    const conversation = page.getByRole("region", { name: "Conversation" });
    await expect(conversation).toBeVisible();

    const comment = "Thanks, taking a look now.";
    await conversation.getByRole("textbox").fill(comment);
    await conversation
      .getByRole("button", { name: "Comment", exact: true })
      .click();
    await expect(conversation.getByText(comment)).toBeVisible();

    await page.getByRole("button", { name: /Review changes/ }).click();
    const verdict = "Approving the constant modules.";
    await page.getByLabel("Review summary").fill(verdict);
    await page.getByRole("radio", { name: /Approve/ }).check();
    await page.getByRole("button", { name: "Submit review" }).click();
    await expect(conversation.getByText(verdict)).toBeVisible();
    await expect(
      conversation.getByText("Approved", { exact: true }),
    ).toBeVisible();

    const fake = await fakePull(page, pr);
    expect(fake.issue_comments.map((item) => item.body)).toContain(comment);
    expect(fake.reviews).toHaveLength(1);
    expect(fake.reviews[0]).toMatchObject({ state: "APPROVED", body: verdict });
  });

  test("drafts a whole-PR review the user submits with a chosen verdict", async ({
    page,
  }) => {
    await openReview(page, pr);
    await openChatTab(page);
    await sendChat(page, "E2E_REVIEW_CHAT_REVIEW draft a review for me");

    const card = chatPanel(page).getByTestId("proposed-review");
    await expect(card).toContainText("Draft review", { timeout: 30_000 });
    const verdict = card.getByRole("radiogroup", { name: "Review verdict" });
    await expect(
      verdict.getByRole("radio", { name: "Comment" }),
    ).toHaveAttribute("aria-checked", "true");
    await expect(verdict.getByRole("radio", { name: "Approve" })).toBeVisible();
    await expect(card.getByLabel("Review body")).toHaveValue(
      "Looks reasonable overall.",
    );

    await verdict.getByRole("radio", { name: "Request changes" }).click();
    await expect(
      verdict.getByRole("radio", { name: "Request changes" }),
    ).toHaveAttribute("aria-checked", "true");
    const body = "Please add tests before merging.";
    await card.getByLabel("Review body").fill(body);

    const submitted = page.waitForResponse(
      (response) =>
        response.url().endsWith(reviewApi(pr, "/submit-review")) &&
        response.request().method() === "POST",
    );
    await card.getByRole("button", { name: "Submit as you" }).click();
    const response = await submitted;
    expect(response.ok(), await response.text()).toBeTruthy();
    await expect(card).toContainText("Review submitted: Request changes");

    const reviews = (await fakePull(page, pr)).reviews;
    expect(reviews).toHaveLength(1);
    expect(reviews[0]).toMatchObject({
      author: SAME_USER.login,
      state: "CHANGES_REQUESTED",
      body,
    });
    expect((await fakePull(page, pr)).standalone_comment_posts).toEqual([]);
  });

  test("opens info and chat as a sheet on a narrow window", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1000, height: 800 });
    await openReview(page, pr);
    const open = page.getByRole("button", { name: "Info & chat" });
    await expect(open).toBeVisible();
    await expect(
      page.getByPlaceholder("Ask anything about this PR…"),
    ).toBeHidden();

    await open.click();
    const sheet = page.getByRole("dialog");
    await expect(sheet).toBeVisible();
    await openChatTab(page);
    await sendChat(page, "E2E_REVIEW_CHAT_PLAIN what does this add?");
    await expect(
      sheet.getByText("The pull request adds two constant modules."),
    ).toBeVisible({ timeout: 30_000 });

    await page.reload();
    await expect(open).toBeVisible();
    await open.click();
    await expect(sheet).toBeVisible();
    await openChatTab(page);
    await expect(
      sheet.getByText("The pull request adds two constant modules."),
    ).toBeVisible();
    await expect(sheet).toBeVisible();
    const input = sheet.getByPlaceholder("Ask anything about this PR…");
    await input.pressSequentially("follow-up question");
    await expect(input).toHaveValue("follow-up question");
    await expect(sheet).toBeVisible();
  });
});
