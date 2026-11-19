import { test, expect, type Page } from "@playwright/test";

import { seedRepoSidebar, typeIntoComposer } from "./helpers/dashboard";

const ADMIN = { login: "alice", email: "alice@example.com" };
const MEMBER = { login: "bob", email: "bob@example.com" };

const DRAFT_SLUG = "staging-box";
const ALT_NAME = "Alt Box";
const ALT_SLUG = "alt-box";
const DEFAULT_WORKSPACE_PROMPT = "Default workspace: run make test.";
const ALT_WORKSPACE_PROMPT = "Alt workspace: run pytest -q.";
async function loginAs(page: Page, user: { login: string; email: string }) {
  const res = await page.request.post("/control/login", { data: user });
  expect(res.ok()).toBeTruthy();
  await seedRepoSidebar(page);
}

const BASE_URL = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;

// The dashboard's mutating routes enforce same-origin, which a browser sets for
// itself but APIRequestContext does not.
const SAME_ORIGIN_HEADERS = { origin: BASE_URL, referer: `${BASE_URL}/` };

async function deleteWorkspace(page: Page, slug: string) {
  await page.request.delete(`/control/workspaces/${slug}`, {
    headers: SAME_ORIGIN_HEADERS,
  });
}

// `default` cannot be deleted, so specs put the seeded row back instead.
async function resetDefaultWorkspace(page: Page) {
  const res = await page.request.post("/control/reset-default-workspace");
  expect(res.ok()).toBeTruthy();
}

async function setDefaultWorkspacePrompt(page: Page, prompt: string) {
  const res = await page.request.post("/control/workspaces", {
    data: { name: "Default", prompt, repos: ["e2e/default"] },
  });
  expect(res.ok()).toBeTruthy();
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

async function createWorkspace(
  page: Page,
  name: string,
  prompt: string,
): Promise<string> {
  // A workspace owns at least one repository and a repository has exactly one
  // owner, so every test workspace claims one named after itself.
  const repo = `e2e/${name.toLowerCase().replace(/\s+/g, "-")}`;
  const created = await page.request.post("/control/workspaces", {
    headers: SAME_ORIGIN_HEADERS,
    data: { name, prompt, repos: [repo] },
  });
  expect(created.ok()).toBeTruthy();
  // No snapshot: the prompt applies on its own, and the sandbox falls back to
  // the base image — which is what these selection specs assert on.
  return ((await created.json()) as { slug: string }).slug;
}

test.describe("Workspaces", () => {
  test("cloud identity form saves federation and governed tags", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    const original = await (
      await page.request.get("/dashboard/api/settings/execution")
    ).json();
    const tag = "00000000-0000-0000-0000-000000000001";
    try {
      await page.goto("/admin");
      const proxy = page
        .getByRole("heading", { name: "Sandbox Proxy", exact: true })
        .locator("xpath=ancestor::section");
      for (const cloud of ["AWS", "GCP", "Azure"]) {
        await proxy
          .getByRole("button", { name: `Add ${cloud} identity`, exact: true })
          .click();
        await expect(
          proxy.getByRole("button", {
            name: `Add ${cloud} identity`,
            exact: true,
          }),
        ).toBeDisabled();
      }
      for (const [label, value] of [
        ["AWS Role ARN", "arn:aws:iam::123456789012:role/ReadOnly"],
        [
          "GCP Workload Identity Provider",
          "//iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/pool/providers/provider",
        ],
        [
          "Service Account Email (Optional)",
          "sandbox@project.iam.gserviceaccount.com",
        ],
        ["Azure Tenant ID", tag],
        ["Azure Client ID", tag],
        ["Azure Resources (Comma-Separated)", "https://storage.azure.com"],
        ["Governed Tag Value IDs", tag],
      ])
        await proxy.getByLabel(label, { exact: true }).fill(value);
      for (const scope of await proxy
        .getByLabel("Subject Scope", { exact: true })
        .all())
        await scope.fill(`tag:${tag}`);
      await proxy.getByRole("tab", { name: "JSON", exact: true }).click();
      await expect
        .poll(async () => {
          const config: { rules: { aws?: { subject_scope: string } }[] } =
            JSON.parse(
              await proxy
                .getByLabel("Proxy configuration (JSON)", { exact: true })
                .inputValue(),
            );
          return config.rules[0].aws?.subject_scope;
        })
        .toBe(`tag:${tag}`);
      const saved = page.waitForResponse(
        (response) =>
          response.url().endsWith("/settings/execution") &&
          response.request().method() === "PATCH",
      );
      await proxy
        .getByRole("button", { name: "Save proxy configuration" })
        .click();
      expect((await saved).status()).toBe(200);
      await page.reload();
      await expect(proxy.getByLabel("Governed Tag Value IDs")).toHaveValue(tag);
      await expect(
        proxy.getByLabel("Subject Scope", { exact: true }).first(),
      ).toHaveValue(`tag:${tag}`);
      await expect(
        proxy.getByLabel("Azure Resources (Comma-Separated)"),
      ).toHaveValue("https://storage.azure.com");
      await proxy.getByLabel("Governed Tag Value IDs").fill("");
      const refused = page.waitForResponse(
        (response) =>
          response.url().endsWith("/settings/execution") &&
          response.request().method() === "PATCH",
      );
      await proxy
        .getByRole("button", { name: "Save proxy configuration" })
        .click();
      expect((await refused).status()).toBe(422);
      await expect(proxy.getByRole("alert")).toBeVisible();
    } finally {
      await page.request.patch("/dashboard/api/settings/execution", {
        headers: SAME_ORIGIN_HEADERS,
        data: { create_params: original.create_params },
      });
    }
  });

  test("cloud environment controls stay absent with Robin-wide Defaults off and on", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    const originalResponse = await page.request.get(
      "/dashboard/api/settings/execution",
    );
    expect(originalResponse.ok()).toBeTruthy();
    const original = (await originalResponse.json()) as { enabled: boolean };
    expect(original.enabled).toBe(false);
    try {
      for (const enabled of [false, true]) {
        await page.goto("/admin");
        const toggle = page.getByRole("switch", {
          name: "Use Robin-wide Defaults",
        });
        if (enabled) await toggle.click();
        await expect(toggle).toBeChecked({ checked: enabled });
        for (const path of [
          "/workspaces",
          `/workspaces/${DRAFT_SLUG}`,
          "/agents/workspaces",
        ]) {
          await page.goto(path);
          await expect(page).toHaveURL(/\/admin$/);
          await expect(toggle).toBeChecked({ checked: enabled });
          await expect(
            page.getByRole("link", { name: "Environments", exact: true }),
          ).toHaveCount(0);
          await expect(
            page.getByRole("button", { name: "Add environment" }),
          ).toHaveCount(0);
        }
        await openNewAgentHome(page);
        await expect(
          page.getByRole("button", { name: "Environment", exact: true }),
        ).toHaveCount(0);
      }
      await page.goto("/admin/integrations");
      await expect(
        page.getByRole("button", { name: "Choose repositories" }),
      ).toBeVisible();
      await expect(
        page.getByRole("button", { name: "Choose channels" }),
      ).toBeVisible();
    } finally {
      const restored = await page.request.patch(
        "/dashboard/api/settings/execution",
        {
          headers: SAME_ORIGIN_HEADERS,
          data: { enabled: original.enabled },
        },
      );
      expect(restored.ok()).toBeTruthy();
    }
  });

  test("a non-admin cannot reach the workspaces page or API", async ({
    page,
  }) => {
    await loginAs(page, MEMBER);

    const res = await page.request.get("/dashboard/api/workspaces");
    expect(res.status()).toBe(403);

    await page.goto("/agents/workspaces");
    await expect(page).toHaveURL(/\/agents$/);
    await expect(
      page.getByRole("heading", { name: "Environments", level: 2 }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^Delete / })).toHaveCount(0);
    await page.goto(`/workspaces/${DRAFT_SLUG}`);
    await expect(page).toHaveURL(/\/agents$/);
    await expect(page.getByRole("button", { name: /^Delete / })).toHaveCount(0);
    // No Admin toggle in the composer, so they cannot start an admin thread.
    await openNewAgentHome(page);
    await expect(page.getByTestId("composer-editor")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Admin mode", exact: true }),
    ).toHaveCount(0);
  });

  test("the composer hides environments while OFF preserves a saved context and its run instructions", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    await resetDefaultWorkspace(page);
    await deleteWorkspace(page, ALT_SLUG);
    await setDefaultWorkspacePrompt(page, DEFAULT_WORKSPACE_PROMPT);
    const preferences = await (
      await page.request.get("/dashboard/api/me/preferences")
    ).json();
    try {
      await createWorkspace(page, ALT_NAME, ALT_WORKSPACE_PROMPT);
      const saved = await page.request.put("/dashboard/api/me/preferences", {
        headers: SAME_ORIGIN_HEADERS,
        data: { ...preferences, default_workspace: ALT_SLUG },
      });
      expect(saved.ok()).toBeTruthy();
      await openNewAgentHome(page);
      await expect(
        page.getByRole("button", { name: "Environment", exact: true }),
      ).toHaveCount(0);
      await typeIntoComposer(page, "Which instructions apply? E2E_FOLLOWUP");
      await expect(page).toHaveURL(/\/agents\/[^/]+$/);
      const threadId = new URL(page.url()).pathname.split("/").pop() ?? "";
      await expect
        .poll(async () => {
          const response = await page.request.get(
            `/dashboard/api/threads/${threadId}?mark_viewed=false`,
          );
          return response.ok()
            ? ((await response.json()) as { workspace?: string }).workspace
            : undefined;
        })
        .toBe(ALT_SLUG);
      await expect
        .poll(() => lastSystemPrompt(page), { timeout: 30_000 })
        .toContain(ALT_WORKSPACE_PROMPT);
      expect(await lastSystemPrompt(page)).not.toContain(
        DEFAULT_WORKSPACE_PROMPT,
      );
    } finally {
      await page.request.put("/dashboard/api/me/preferences", {
        headers: SAME_ORIGIN_HEADERS,
        data: preferences,
      });
      await deleteWorkspace(page, ALT_SLUG);
      await resetDefaultWorkspace(page);
    }
  });

  test("a /workspace: command on the opening Slack message selects the workspace", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    await deleteWorkspace(page, ALT_SLUG);
    await createWorkspace(page, ALT_NAME, ALT_WORKSPACE_PROMPT);

    await page.goto("/mock/slack");
    await page.locator("#reset").click();
    await expect(page.locator("#thread")).toContainText("No messages yet");
    await page
      .locator("#text")
      .fill(
        `<@U0BOT> /workspace:${ALT_SLUG} please add a greet() helper and open a PR`,
      );
    await page.locator("#send").click();

    await expect(
      page.locator(".msg.bot").filter({ hasText: "Add greet() helper" }),
    ).toBeVisible();

    const systemPrompt = await lastSystemPrompt(page);
    expect(systemPrompt).toContain(ALT_WORKSPACE_PROMPT);
    // The command itself is consumed, so the agent never sees it in the request.
    expect(systemPrompt).not.toContain(`/workspace:${ALT_SLUG}`);

    await deleteWorkspace(page, ALT_SLUG);
  });
});
