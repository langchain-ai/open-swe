import { test, expect, type Page } from "@playwright/test";

// Threads have no owner: every authenticated user has the same rights on every
// thread. What differs per user is only which threads their sidebar lists —
// the ones they have posted in.
const ALICE = { login: "alice", email: "alice@example.com" };
const BOB = { login: "bob", email: "bob@example.com" };
const BOB_SLACK_ID = "U_BOB";

async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

// Start a Slack thread as `sender` and land on the real dashboard app via the
// bot's own "Open in Web" link. Returns the agent thread id.
async function openThreadViaSlack(
  page: Page,
  options: { sender?: string; message?: string } = {},
): Promise<string> {
  await page.goto("/mock/slack");
  await page.locator("#reset").click();
  await expect(page.locator("#thread")).toContainText("No messages yet");
  if (options.sender) {
    await page.locator("#user").selectOption(options.sender);
  }
  await page
    .locator("#text")
    .fill(
      options.message ?? "<@U0BOT> please add a greet() helper and open a PR",
    );
  await page.locator("#send").click();

  const webLink = page.locator('.msg.bot a[href*="/agents/"]').first();
  await expect(webLink).toBeVisible();
  await webLink.click();
  await expect(page).toHaveURL(/\/agents\//);
  const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
  expect(threadId).not.toBe("");
  return threadId;
}

// The SDK hydrates an idle thread's transcript from getState on load, which can
// briefly lag; the composer only renders once it has. Reload until it is there.
async function expectComposerReady(page: Page) {
  await expect(async () => {
    await page.reload();
    await expect(page.getByTestId("composer-editor")).toBeVisible({
      timeout: 8000,
    });
  }).toPass({ timeout: 60_000 });
}

async function sidebarThreadIds(page: Page): Promise<Array<string>> {
  // `include_projects` keeps project threads in the list instead of leaving
  // them to the per-project folders, so this stays a flat "is it mine" check.
  const res = await page.request.get(
    "/dashboard/api/threads/sidebar?limit=50&include_resolved=true&include_projects=true",
  );
  expect(res.ok(), await res.text()).toBeTruthy();
  const payload = (await res.json()) as {
    recents: { items: Array<{ id: string }> };
  };
  return payload.recents.items.map((item) => item.id);
}

async function typeIntoComposer(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

async function waitForStateToContain(
  page: Page,
  threadId: string,
  text: string,
) {
  await expect
    .poll(
      async () => {
        const res = await page.request.get(
          `/dashboard/api/threads/${threadId}/state`,
        );
        if (!res.ok()) return false;
        return JSON.stringify(await res.json()).includes(text);
      },
      { timeout: 60_000, intervals: [500] },
    )
    .toBe(true);
}

test.describe("ownerless threads", () => {
  // The sidebar used to be scoped by `metadata.github_login` — whoever created
  // the thread. It is now scoped by participation, so posting is what puts a
  // thread on your list.
  test("the sidebar lists threads you have posted in, not ones you created", async ({
    page,
    browser,
    baseURL,
  }) => {
    test.slow();
    await loginAs(page, ALICE);
    const threadId = await openThreadViaSlack(page);

    await expect
      .poll(() => sidebarThreadIds(page), { timeout: 30_000 })
      .toContain(threadId);

    const bobContext = await browser.newContext({ baseURL });
    const bob = await bobContext.newPage();
    await loginAs(bob, BOB);

    // Bob has not posted here, so it is not on his list…
    expect(await sidebarThreadIds(bob)).not.toContain(threadId);

    // …but he can open it and post, exactly like anyone else.
    await bob.goto(`/agents/${threadId}`);
    await expect(bob).toHaveURL(new RegExp(`/agents/${threadId}$`));
    await expectComposerReady(bob);
    await typeIntoComposer(bob, "Can you also add a docstring?");
    await waitForStateToContain(bob, threadId, "add a docstring");

    // Posting joins him to the thread; both users now list it.
    await expect
      .poll(() => sidebarThreadIds(bob), { timeout: 30_000 })
      .toContain(threadId);
    await expect
      .poll(() => sidebarThreadIds(page), { timeout: 30_000 })
      .toContain(threadId);

    await bobContext.close();
  });

  // Resolve, unresolve, cancel and delete were all owner-only and answered 404
  // for everyone else. They are ordinary actions now.
  test("a user who did not create the thread can resolve and delete it", async ({
    page,
    browser,
    baseURL,
  }) => {
    await loginAs(page, ALICE);
    const threadId = await openThreadViaSlack(page);

    const bobContext = await browser.newContext({ baseURL });
    const bob = await bobContext.newPage();
    await loginAs(bob, BOB);

    const headers = { origin: baseURL ?? "", referer: `${baseURL ?? ""}/` };
    const resolved = await bob.request.post(
      `/dashboard/api/threads/${threadId}/resolve`,
      { data: { resolved: true }, headers },
    );
    expect(resolved.ok(), await resolved.text()).toBeTruthy();
    expect(((await resolved.json()) as { resolved: boolean }).resolved).toBe(
      true,
    );

    const deleted = await bob.request.delete(
      `/dashboard/api/threads/${threadId}`,
      { headers },
    );
    expect([200, 204]).toContain(deleted.status());

    // Gone for the person who started it too.
    const afterDelete = await page.request.get(
      `/dashboard/api/threads/${threadId}?mark_viewed=false`,
    );
    expect(afterDelete.status()).toBe(404);

    await bobContext.close();
  });

  // Whoever sends the message is the thread's context for that turn; the person
  // who opened the thread carries no standing privilege over later senders.
  test("a thread started by one user lists for the other once they reply in Slack", async ({
    page,
    browser,
    baseURL,
  }) => {
    await loginAs(page, ALICE);
    const threadId = await openThreadViaSlack(page, { sender: BOB_SLACK_ID });

    const bobContext = await browser.newContext({ baseURL });
    const bob = await bobContext.newPage();
    await loginAs(bob, BOB);

    // Bob sent the Slack message, so it is his thread to see — Alice, who has
    // said nothing in it, does not list it even though she is signed in.
    await expect
      .poll(() => sidebarThreadIds(bob), { timeout: 30_000 })
      .toContain(threadId);
    expect(await sidebarThreadIds(page)).not.toContain(threadId);

    await bobContext.close();
  });
});
