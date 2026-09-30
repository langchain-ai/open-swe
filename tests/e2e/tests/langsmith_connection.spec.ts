import { expect, test, type Page } from "@playwright/test";

import { loginAs, SAME_USER } from "./helpers/dashboard";

type Status = {
  connected: boolean;
  method: "oauth" | "api_key" | null;
  region: "us" | "eu" | "apac";
  email: string | null;
  name: string | null;
  workspace_id: string | null;
  reconnect_required: boolean;
};

const disconnected: Status = {
  connected: false,
  method: null,
  region: "us",
  email: null,
  name: null,
  workspace_id: null,
  reconnect_required: false,
};
const credentialURL = "**/dashboard/api/my-credentials/langsmith";

function connectionRow(page: Page) {
  return page
    .locator('div[class*="py-3.5"]')
    .filter({ has: page.getByText("LangSmith", { exact: true }) });
}

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test("LangSmith API-key validation, failure recovery and disconnect", async ({
  page,
}) => {
  let status = { ...disconnected };
  let rejectSave = false;
  let rejectDisconnect = true;
  const saveGate = deferred();
  await loginAs(page, SAME_USER);
  await page.route(credentialURL, async (route) => {
    if (route.request().method() === "PUT") {
      if (rejectSave) {
        await route.fulfill({
          status: 400,
          json: { detail: "Synthetic LangSmith rejected the credential" },
        });
        return;
      }
      await saveGate.promise;
      status = {
        ...disconnected,
        connected: true,
        method: "api_key",
        region: "eu",
        workspace_id: "bafcc5e4-bf13-4000-b078-b093504e16c1",
      };
    }
    if (route.request().method() === "DELETE") {
      if (rejectDisconnect) {
        await route.fulfill({
          status: 503,
          json: { detail: "Synthetic disconnect failure" },
        });
        return;
      }
      status = { ...disconnected };
    }
    await route.fulfill({ json: status });
  });
  await page.goto("/my-settings");
  const row = connectionRow(page);
  const dialog = page.getByRole("dialog");
  await row.getByRole("button", { name: "Connect", exact: true }).click();
  await dialog.getByRole("button", { name: "Use API key instead" }).click();
  await dialog
    .getByLabel("API key", { exact: true })
    .fill("synthetic-discarded-key");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await row.getByRole("button", { name: "Connect", exact: true }).click();
  await dialog.getByRole("button", { name: "Use API key instead" }).click();
  await expect(dialog.getByLabel("API key", { exact: true })).toHaveValue("");
  await dialog.getByLabel("LangSmith region").selectOption("eu");
  await dialog
    .getByLabel("API key", { exact: true })
    .fill("synthetic-valid-key");
  await dialog
    .getByLabel("Workspace ID (optional)")
    .fill("bafcc5e4-bf13-4000-b078-b093504e16c1");
  const saving = page.waitForRequest(
    (request) =>
      request.url().endsWith("/my-credentials/langsmith") &&
      request.method() === "PUT",
  );
  await dialog.getByRole("button", { name: "Save API key" }).click();
  expect((await saving).postDataJSON()).toEqual({
    api_key: "synthetic-valid-key",
    region: "eu",
    workspace_id: "bafcc5e4-bf13-4000-b078-b093504e16c1",
  });
  await expect(
    dialog.getByRole("button", { name: "Validating…" }),
  ).toBeDisabled();
  await expect(row.getByText("Not connected", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("API key", { exact: true })).toHaveValue("");
  saveGate.resolve();
  await expect(dialog).toBeHidden();
  await expect(row).toContainText("Connected with an API key · EU");
  await page.reload();
  await expect(row).toContainText("Connected with an API key · EU");

  rejectSave = true;
  await row.getByRole("button", { name: "Reconnect" }).click();
  await expect(dialog.getByLabel("LangSmith region")).toHaveValue("eu");
  await dialog.getByRole("button", { name: "Use API key instead" }).click();
  await dialog
    .getByLabel("API key", { exact: true })
    .fill("synthetic-invalid-key");
  await dialog.getByRole("button", { name: "Save API key" }).click();
  await expect(page.locator("[data-sonner-toast]")).toContainText(
    "Couldn't connect LangSmith",
  );
  await expect(row).toContainText("Connected with an API key · EU");
  await expect(dialog.getByLabel("API key", { exact: true })).toHaveValue("");
  await dialog.getByRole("button", { name: "Cancel" }).click();

  await row.getByRole("button", { name: "Disconnect" }).click();
  await expect(
    page
      .locator("[data-sonner-toast]")
      .filter({ hasText: "Couldn't disconnect LangSmith" }),
  ).toBeVisible();
  await expect(row.getByText("Connected", { exact: true })).toBeVisible();
  rejectDisconnect = false;
  await row.getByRole("button", { name: "Disconnect" }).click();
  await expect(row.getByText("Not connected", { exact: true })).toBeVisible();
  await expect(row.getByRole("button", { name: "Disconnect" })).toBeHidden();
  await page.reload();
  await expect(
    row.getByRole("button", { name: "Connect", exact: true }),
  ).toBeEnabled();
});

test("expired LangSmith authorization reconnects in the selected region", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await page.route(credentialURL, (route) =>
    route.fulfill({
      json: {
        ...disconnected,
        method: "oauth",
        region: "apac",
        reconnect_required: true,
      } satisfies Status,
    }),
  );
  await page.route("**/dashboard/api/langsmith/login?region=*", (route) =>
    route.fulfill({
      contentType: "text/plain",
      body: "Synthetic LangSmith OAuth provider boundary",
    }),
  );
  await page.goto("/my-settings");
  const row = connectionRow(page);
  await expect(row).toContainText("Authorization expired");
  await row.getByRole("button", { name: "Reconnect" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("LangSmith region")).toHaveValue("apac");
  await dialog.getByLabel("LangSmith region").selectOption("eu");
  await dialog.getByRole("button", { name: "Authorize LangSmith" }).click();
  await expect(page).toHaveURL(
    /\/dashboard\/api\/langsmith\/login\?region=eu$/,
  );
  await expect(
    page.getByText("Synthetic LangSmith OAuth provider boundary"),
  ).toBeVisible();
});
