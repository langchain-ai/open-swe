import { test, expect, type Page } from "@playwright/test";
import { loginAs, SAME_ORIGIN_HEADERS, SAME_USER } from "./helpers/dashboard";

const REPO = "fakeorg/demo";

async function resetDefaultWorkspace(page: Page) {
  await page.request.delete(
    `/dashboard/api/workspaces/default/workflow-policies/${REPO}/deploy.yml`,
    { headers: SAME_ORIGIN_HEADERS },
  );
  const res = await page.request.post("/control/reset-default-workspace");
  expect(res.ok()).toBeTruthy();
}

test.beforeEach(async ({ page }) => {
  await loginAs(page, SAME_USER);
  await resetDefaultWorkspace(page);
});

test.afterEach(async ({ page }) => {
  await resetDefaultWorkspace(page);
});

test("admins allow a workflow for a newly bound repository without reloading", async ({
  page,
}) => {
  await page.goto("/workspaces/default");
  const section = page.locator("section").filter({
    has: page.getByRole("heading", { name: "GitHub Workflows", exact: true }),
  });
  await expect(
    section.getByText("Bind a repository to this workspace first."),
  ).toBeVisible();
  await expect(
    section.getByRole("button", { name: "Add workflow" }),
  ).toBeDisabled();

  await page.getByRole("button", { name: "Choose repositories" }).click();
  await page.getByRole("checkbox", { name: REPO }).check();
  await page.getByRole("button", { name: "Save 1 repository" }).click();
  await page.getByRole("button", { name: "Save", exact: true }).click();

  await expect(
    page.getByRole("switch", { name: `Let ${REPO} start threads` }),
  ).toBeVisible();
  await section.getByRole("button", { name: "Add workflow" }).click();
  await section.getByRole("combobox", { name: "Workflow repository" }).click();
  await page.getByRole("option", { name: REPO }).click();

  await section.getByRole("combobox", { name: "Workflow file" }).click();
  await expect(page.getByRole("option", { name: "release.yml" })).toBeVisible();
  await page.getByRole("option", { name: "deploy.yml" }).click();
  await section.getByRole("combobox", { name: "Workflow ref" }).click();
  await expect(page.getByRole("option", { name: "v1.0.0" })).toBeVisible();
  await page.getByRole("option", { name: "release", exact: true }).click();

  await section.getByRole("button", { name: "Add input" }).click();
  await section.getByLabel("Input 1 name").fill("environment");
  await section.getByLabel("Input 1 value").fill("staging");
  await section.getByRole("switch", { name: "Admin threads only" }).click();
  await section.getByRole("button", { name: "Save workflow" }).click();

  const row = section.getByTestId("workflow-policy");
  await expect(row).toContainText(`${REPO} · deploy.yml`);
  await expect(row.getByText("release", { exact: true })).toBeVisible();
  await expect(row.getByText("Admin threads only")).toBeVisible();

  await page.reload();
  await expect(row).toContainText(`${REPO} · deploy.yml`);
  const saved = await page.request.get(
    "/dashboard/api/workspaces/default/workflow-policies",
  );
  expect(await saved.json()).toEqual([
    expect.objectContaining({
      repository: REPO,
      workflow: "deploy.yml",
      ref: "release",
      fixed_inputs: { environment: "staging" },
      allow_bot_inputs: false,
      admin_only: true,
    }),
  ]);

  await section
    .getByRole("button", { name: `Remove ${REPO} deploy.yml` })
    .click();
  await expect(row).toHaveCount(0);
});
