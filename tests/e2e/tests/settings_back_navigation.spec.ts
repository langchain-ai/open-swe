import { expect, test } from "@playwright/test";

import {
  expectTranscriptVisible,
  loginAs,
  openThreadViaSlackLink,
  SAME_USER,
} from "./helpers/dashboard";

test("Back to app returns to the thread that opened settings", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await openThreadViaSlackLink(page);
  // The link navigation resolves before hydration; wait for client content.
  await expectTranscriptVisible(page);
  const threadUrl = page.url();

  await page.getByRole("button", { name: SAME_USER.login }).click();
  await page.getByRole("menuitem", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/my-settings$/);

  await page.getByRole("button", { name: "Back to app" }).click();
  await expect(page).toHaveURL(threadUrl);
});

test("Settings Connections preserves Slack recovery and the main app has its own entry", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  const error = "Slack recovery expired or changed — connect Slack again";
  const query = new URLSearchParams({
    slack_recovery: "test-recovery-token",
    recovery_error: error,
  });
  await page.goto(`/my-settings/connections?${query}`);
  const dialog = page.getByRole("dialog", {
    name: "Recover your Robin account",
  });
  await expect(dialog.getByRole("alert")).toHaveText(error);
  await expect(page).toHaveURL(/\/my-settings\/connections$/);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(
    page.getByRole("heading", { name: "Connections", level: 1 }),
  ).toBeVisible();
  await expect(page.getByText("GitHub", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Back to app" }).click();
  const connections = page.getByRole("link", {
    name: "Connections",
    exact: true,
  });
  await expect(connections).toHaveAttribute("href", "/agents/connections");
  await connections.click();
  await expect(page).toHaveURL(/\/agents\/connections$/);
  await expect(
    page.getByRole("heading", { name: "Connections", level: 1 }),
  ).toBeVisible();
  await expect(page.getByText("GitHub", { exact: true })).toBeVisible();
  await expect(connections).toHaveAttribute("aria-current", "page");
});

test("Connections search finds built-in and custom integrations, leaving accounts in place", async ({
  page,
}) => {
  await page.route("**/dashboard/api/my-managed-tools", (route) =>
    route.fulfill({
      json: {
        configured: true,
        langsmith_connected: true,
        servers: [
          {
            id: "linear",
            name: "Linear",
            kind: "oauth",
            connected: true,
            has_credential: true,
            enabled: true,
            personal: false,
          },
          {
            id: "custom",
            name: "Linear workspace MCP",
            kind: "oauth",
            connected: false,
            has_credential: false,
            enabled: true,
            personal: true,
          },
        ],
      },
    }),
  );
  await loginAs(page, SAME_USER);
  await page.goto("/agents/connections");
  const search = page.getByRole("textbox", { name: "Search integrations" });
  await expect(page.getByText("Linear", { exact: true })).toBeVisible();
  await page.keyboard.press("ControlOrMeta+f");
  await expect(search).toBeFocused();
  await search.fill("  LiNeAr  ");
  await expect(page.getByText("Linear", { exact: true })).toBeVisible();
  await expect(page.getByText("Linear workspace MCP")).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "Accounts" })
      .getByText("GitHub", { exact: true }),
  ).toBeVisible();

  await page.keyboard.press("ControlOrMeta+f");
  await page.keyboard.type("workspace");
  await expect(search).toHaveValue("workspace");
  await expect(page.getByText("Linear workspace MCP")).toBeVisible();
  await expect(page.getByText("Linear", { exact: true })).toHaveCount(0);

  await search.press("Escape");
  await expect(search).toHaveValue("");
  await expect(page.getByText("Linear", { exact: true })).toBeVisible();
  await search.fill("no-such-connection");
  await expect(
    page.getByText("No integrations match “no-such-connection”."),
  ).toBeVisible();
  await page.getByRole("button", { name: "Clear search" }).click();
  await expect(search).toBeFocused();
  await expect(page.getByText("Linear workspace MCP")).toBeVisible();
});
