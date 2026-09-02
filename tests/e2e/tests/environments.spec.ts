import { test, expect, type Page } from "@playwright/test";

// Environments end-to-end: dashboard management, the admin gate, and an admin
// thread that creates, provisions, and captures its own sandbox. The agent, the
// tools, store writes, and prompt injection are real; only the LLM and snapshot
// service are faked (see patches.py).
const ADMIN = { login: "alice", email: "alice@example.com" };
const MEMBER = { login: "bob", email: "bob@example.com" };

const DEFAULT_SLUG = "default";
const DRAFT_NAME = "Staging Box";
const DRAFT_SLUG = "staging-box";
const EXPECTED_SNAPSHOT_NAME = "openswe-environment-default";
const ALT_NAME = "Alt Box";
const ALT_SLUG = "alt-box";
const DEFAULT_ENV_PROMPT = "Default environment: run make test.";
const ALT_ENV_PROMPT = "Alt environment: run pytest -q.";
// Mirrors fake_llm.py's environment script.
const ENVIRONMENT_PROMPT =
  "Checkouts live in /workspace/repos. Build with `make build`, test with `make test`.";

interface Environment {
  slug: string;
  name: string;
  prompt: string;
  repos: Array<string>;
  snapshot_id: string | null;
  snapshot_name: string | null;
  snapshot_status: string;
}

async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
}

async function listEnvironments(page: Page): Promise<Array<Environment>> {
  const res = await page.request.get("/dashboard/api/environments");
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { environments: Array<Environment> })
    .environments;
}

async function findEnvironment(
  page: Page,
  slug: string,
): Promise<Environment | undefined> {
  return (await listEnvironments(page)).find((env) => env.slug === slug);
}

const BASE_URL = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;

// The dashboard's mutating routes enforce same-origin, which a browser sets for
// itself but APIRequestContext does not.
const SAME_ORIGIN_HEADERS = { origin: BASE_URL, referer: `${BASE_URL}/` };

async function deleteEnvironment(page: Page, slug: string) {
  await page.request.delete(`/dashboard/api/environments/${slug}`, {
    headers: SAME_ORIGIN_HEADERS,
  });
}

// Saving a default model retires the first-run onboarding modal, which otherwise
// covers the composer on the new-agent page.
async function saveDefaultModel(page: Page) {
  const options = (await (
    await page.request.get("/dashboard/api/options")
  ).json()) as {
    default_agent_model: string;
    default_agent_reasoning_effort: string;
  };
  const res = await page.request.put("/dashboard/api/profile", {
    headers: SAME_ORIGIN_HEADERS,
    data: {
      default_model: options.default_agent_model,
      reasoning_effort: options.default_agent_reasoning_effort,
    },
  });
  expect(res.ok()).toBeTruthy();
}

async function capturedSnapshots(
  page: Page,
): Promise<Array<{ snapshot_id: string; name: string; sandbox_id: string }>> {
  const res = await page.request.get("/control/snapshots");
  expect(res.ok()).toBeTruthy();
  return (
    (await res.json()) as {
      captured: Array<{
        snapshot_id: string;
        name: string;
        sandbox_id: string;
      }>;
    }
  ).captured;
}

async function lastSystemPrompt(page: Page): Promise<string> {
  const res = await page.request.get("/control/last-system-prompt");
  expect(res.ok()).toBeTruthy();
  return ((await res.json()) as { text: string }).text;
}

async function openNewAgentHome(page: Page) {
  await saveDefaultModel(page);
  await page.goto("/agents");
  await expect(page.getByTestId("composer-editor")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
}

async function createEnvironment(
  page: Page,
  name: string,
  prompt: string,
): Promise<string> {
  const created = await page.request.post("/dashboard/api/environments", {
    headers: SAME_ORIGIN_HEADERS,
    data: { name, prompt },
  });
  expect(created.ok()).toBeTruthy();
  // No snapshot: the prompt applies on its own, and the sandbox falls back to
  // the base image — which is what these selection specs assert on.
  return ((await created.json()) as { slug: string }).slug;
}

async function typeIntoComposer(page: Page, text: string) {
  const editor = page.getByTestId("composer-editor");
  await editor.click();
  await editor.pressSequentially(text);
  await editor.press("Enter");
}

test.describe("Environments", () => {
  test("an admin views environments and editing instructions in Settings", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    await deleteEnvironment(page, DRAFT_SLUG);
    await createEnvironment(page, DRAFT_NAME, "");

    await page.goto("/my-settings");
    const section = page
      .getByRole("heading", { name: "Environments" })
      .locator("xpath=ancestor::section");
    await expect(section).toBeVisible();
    await expect(section.getByText(DRAFT_NAME)).toBeVisible();
    await expect(section.getByText("No snapshot").first()).toBeVisible();
    await expect(section.getByText(/enable admin mode/)).toBeVisible();
    await expect(section.getByRole("button", { name: "Save" })).toHaveCount(0);
    await expect(section.getByRole("button", { name: "Delete" })).toHaveCount(
      0,
    );

    await deleteEnvironment(page, DRAFT_SLUG);
  });

  test("a non-admin cannot reach the environments page or API", async ({
    page,
  }) => {
    await loginAs(page, MEMBER);

    const res = await page.request.get("/dashboard/api/environments");
    expect(res.status()).toBe(403);

    await page.goto("/agents/environments");
    await expect(page).toHaveURL(/\/my-settings/);
    await expect(
      page.getByRole("heading", { name: "Environments" }),
    ).toBeVisible();
    await expect(page.getByText(/ask a workspace admin/)).toBeVisible();

    // No Admin toggle in the composer, so they cannot start an admin thread.
    await openNewAgentHome(page);
    await expect(page.getByTestId("composer-editor")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Admin mode", exact: true }),
    ).toHaveCount(0);
  });

  test("the composer picker appears only with several environments, and the pick reaches the run", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    await deleteEnvironment(page, DEFAULT_SLUG);
    await deleteEnvironment(page, ALT_SLUG);
    await createEnvironment(page, "default", DEFAULT_ENV_PROMPT);

    // One environment: the choice is already made, so no control is rendered.
    await openNewAgentHome(page);
    await expect(page.getByRole("button", { name: "Environment" })).toHaveCount(
      0,
    );

    await createEnvironment(page, ALT_NAME, ALT_ENV_PROMPT);
    await page.reload();
    const picker = page.getByRole("button", { name: "Environment" });
    await expect(picker).toBeVisible();
    // Defaults to the environment named `default`.
    await expect(picker).toContainText("default");

    await picker.click();
    await page.getByRole("button", { name: new RegExp(ALT_NAME) }).click();
    await expect(picker).toContainText(ALT_NAME);

    await typeIntoComposer(page, "Which environment am I in?");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";

    // The thread records the pick, and the run's prompt carries that
    // environment's instructions — not the default's.
    await expect
      .poll(async () => {
        const res = await page.request.get(
          `/dashboard/api/threads/${threadId}?mark_viewed=false`,
        );
        return res.ok()
          ? ((await res.json()) as { environment?: string | null }).environment
          : undefined;
      })
      .toBe(ALT_SLUG);
    await expect
      .poll(() => lastSystemPrompt(page), { timeout: 30_000 })
      .toContain(ALT_ENV_PROMPT);
    expect(await lastSystemPrompt(page)).not.toContain(DEFAULT_ENV_PROMPT);

    await deleteEnvironment(page, ALT_SLUG);
    await deleteEnvironment(page, DEFAULT_SLUG);
  });

  test("an env: tag on the opening Slack message selects the environment", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    await deleteEnvironment(page, ALT_SLUG);
    await createEnvironment(page, ALT_NAME, ALT_ENV_PROMPT);

    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
    await page
      .locator("#text")
      .fill(
        `<@U0BOT> env:${ALT_SLUG} please add a greet() helper and open a PR`,
      );
    await page.locator("#send").click();

    await expect(
      page.locator(".msg.bot").filter({ hasText: "Add greet() helper" }),
    ).toBeVisible();

    const systemPrompt = await lastSystemPrompt(page);
    expect(systemPrompt).toContain(ALT_ENV_PROMPT);
    // The tag itself is consumed, so the agent never sees it in the request.
    expect(systemPrompt).not.toContain(`env:${ALT_SLUG}`);

    await deleteEnvironment(page, ALT_SLUG);
  });

  test("an admin thread provisions its sandbox, captures it, and later runs boot with the environment prompt", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    await deleteEnvironment(page, DEFAULT_SLUG);
    await page.request.post("/control/reset");

    await openNewAgentHome(page);
    const adminToggle = page.getByRole("button", {
      name: "Admin mode",
      exact: true,
    });
    await expect(adminToggle).toBeVisible({ timeout: 20_000 });
    await expect(adminToggle).toHaveAttribute("aria-pressed", "false");
    await adminToggle.click();
    await expect(adminToggle).toHaveAttribute("aria-pressed", "true");

    await typeIntoComposer(
      page,
      "Please set up the default environment for this repo and capture it.",
    );
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
    expect(threadId).not.toBe("");

    // The agent's own summary, after the real save + capture tools ran.
    await expect(
      page.getByText(/environment is captured and live/),
    ).toBeVisible();

    // The record the real tools wrote: prompt, repos, and a ready snapshot.
    const record = await findEnvironment(page, DEFAULT_SLUG);
    expect(record).toBeDefined();
    expect(record?.prompt).toBe(ENVIRONMENT_PROMPT);
    expect(record?.repos).toEqual(["fakeorg/demo"]);
    expect(record?.snapshot_status).toBe("ready");
    expect(record?.snapshot_name).toBe(EXPECTED_SNAPSHOT_NAME);

    // The capture went to the platform against this thread's own sandbox.
    const captures = await capturedSnapshots(page);
    expect(captures.map((c) => c.name)).toEqual([EXPECTED_SNAPSHOT_NAME]);
    expect(captures[0]?.snapshot_id).toBe(record?.snapshot_id);
    const threadRes = await page.request.get(
      `/dashboard/api/threads/${threadId}?mark_viewed=false`,
    );
    expect(threadRes.ok()).toBeTruthy();
    const thread = (await threadRes.json()) as { sandboxId?: string | null };
    expect(captures[0]?.sandbox_id).toBe(thread.sandboxId);

    // A later run is told about the environment: the prompt is appended verbatim.
    await typeIntoComposer(page, "Thanks — anything else needed?");
    await expect(
      page.getByText(/anything else you'd like changed/),
    ).toBeVisible();
    const systemPrompt = await lastSystemPrompt(page);
    expect(systemPrompt).toContain("### Environment Instructions (default)");
    expect(systemPrompt).toContain(ENVIRONMENT_PROMPT);
    // Admin threads also carry the environment-management instructions.
    expect(systemPrompt).toContain("### Admin Thread: Workspace Setup");

    await page.goto("/my-settings");
    await expect(page.getByText("Default environment")).toBeVisible();
    await expect(page.getByText("Snapshot ready")).toBeVisible();
    await expect(page.getByRole("button", { name: "Delete" })).toHaveCount(0);

    // Leave no default behind: later specs' runs would boot from it.
    await deleteEnvironment(page, DEFAULT_SLUG);
    await expect
      .poll(() => findEnvironment(page, DEFAULT_SLUG))
      .toBeUndefined();
  });
});
