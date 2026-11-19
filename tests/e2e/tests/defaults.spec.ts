import { test, expect, type Page } from "@playwright/test";

import {
  loginAs,
  SAME_ORIGIN_HEADERS,
  typeIntoComposer,
} from "./helpers/dashboard";

const ADMIN = { login: "alice", email: "alice@example.com" };
const MEMBER = { login: "bob", email: "bob@example.com" };

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

test.describe("Global Defaults and routing", () => {
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

  test("GitHub Actions permission is an explicit, persistent repository setting", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    const path = "/dashboard/api/routing/repositories/fakeorg/demo";
    const response = await page.request.get("/dashboard/api/routing");
    expect(response.ok()).toBeTruthy();
    const routing = (await response.json()) as {
      repos: Array<{ repo: string; may_start_threads: boolean }>;
    };
    const original = routing.repos.find((row) => row.repo === "fakeorg/demo");
    try {
      const seeded = await page.request.put(path, {
        headers: SAME_ORIGIN_HEADERS,
        data: { may_start_threads: false },
      });
      expect(seeded.ok()).toBeTruthy();
      await page.goto("/admin/integrations");
      const permission = page.getByRole("switch", {
        name: "Let fakeorg/demo start threads",
        exact: true,
      });
      await expect(permission).not.toBeChecked();
      const saved = page.waitForResponse(
        (item) =>
          item.url().endsWith(path) && item.request().method() === "PUT",
      );
      await permission.click();
      expect((await saved).status()).toBe(200);
      await page.reload();
      await expect(permission).toBeChecked();
    } finally {
      const restored = original
        ? await page.request.put(path, {
            headers: SAME_ORIGIN_HEADERS,
            data: { may_start_threads: original.may_start_threads },
          })
        : await page.request.delete(path, { headers: SAME_ORIGIN_HEADERS });
      expect(restored.ok()).toBeTruthy();
    }
  });

  test("global execution instructions always apply to new runs", async ({
    page,
  }) => {
    await loginAs(page, ADMIN);
    const response = await page.request.get(
      "/dashboard/api/settings/execution",
    );
    expect(response.ok()).toBeTruthy();
    const original = (await response.json()) as { prompt: string };
    const prompt =
      "Global Robin instructions: use the shared execution defaults.";
    try {
      const saved = await page.request.patch(
        "/dashboard/api/settings/execution",
        {
          headers: SAME_ORIGIN_HEADERS,
          data: { prompt },
        },
      );
      expect(saved.ok()).toBeTruthy();
      await page.goto("/admin");
      await expect(
        page.getByRole("switch", { name: "Use Robin-wide Defaults" }),
      ).toHaveCount(0);
      await openNewAgentHome(page);
      await expect(
        page.getByRole("button", { name: "Environment", exact: true }),
      ).toHaveCount(0);
      await typeIntoComposer(page, "Which instructions apply? E2E_FOLLOWUP");
      await expect(page).toHaveURL(/\/agents\/[^/]+$/);
      await expect
        .poll(() => lastSystemPrompt(page), { timeout: 30_000 })
        .toContain(prompt);
    } finally {
      const restored = await page.request.patch(
        "/dashboard/api/settings/execution",
        {
          headers: SAME_ORIGIN_HEADERS,
          data: { prompt: original.prompt },
        },
      );
      expect(restored.ok()).toBeTruthy();
    }
  });

  test("a member cannot edit global execution or routing settings", async ({
    page,
  }) => {
    await loginAs(page, MEMBER);
    const execution = await page.request.patch(
      "/dashboard/api/settings/execution",
      {
        headers: SAME_ORIGIN_HEADERS,
        data: { prompt: "unauthorized" },
      },
    );
    expect(execution.status()).toBe(403);
    const routing = await page.request.put(
      "/dashboard/api/routing/repositories/fakeorg/demo",
      {
        headers: SAME_ORIGIN_HEADERS,
        data: { may_start_threads: true },
      },
    );
    expect(routing.status()).toBe(403);
    await openNewAgentHome(page);
    await expect(
      page.getByRole("button", { name: "Admin mode", exact: true }),
    ).toHaveCount(0);
  });
});
