import { expect, test } from "@playwright/test";

const harness = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;

test("Alice creates and transitions private threads; Bob is denied", async ({
  page,
  playwright,
}) => {
  const api = await playwright.request.newContext({ baseURL: harness });
  const id = crypto.randomUUID();
  let createdId: string | undefined;
  try {
    const seed = await api.post("/threads", {
      data: {
        thread_id: id,
        metadata: {
          title: "Private project planning",
          source: "dashboard",
          owner_login: "alice",
          visibility: "public",
        },
      },
    });
    expect(seed.ok()).toBeTruthy();
    await page.request.post("/control/login", { data: { login: "bob" } });
    await page.goto(`/agents/${id}`);
    await expect(
      page.getByText("Private project planning", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Make private", exact: true }),
    ).toHaveCount(0);
    await page.request.post("/control/login", { data: { login: "alice" } });
    await page.goto("/agents");
    await page.getByRole("button", { name: "Maybe later" }).click();
    await page.getByLabel("Visibility").selectOption("private");
    const editor = page.getByTestId("composer-editor");
    await editor.fill("Private planning notes");
    await editor.press("Enter");
    await expect(page).toHaveURL(/\/agents\/[^/]+$/);
    createdId = new URL(page.url()).pathname.split("/").pop()!;
    await expect
      .poll(async () => {
        const response = await page.request.get(
          `/dashboard/api/threads/${createdId}`,
        );
        return response.ok() ? (await response.json()).visibility : null;
      })
      .toBe("private");
    await page.goto(`/agents/${id}`);
    await page
      .getByRole("button", { name: "Make private", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText(
      "Previously shared copies cannot be retracted.",
    );
    await dialog
      .getByRole("button", { name: "Make private", exact: true })
      .click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByText("Private", { exact: true })).toBeVisible();
    await page.request.post("/control/login", { data: { login: "bob" } });
    await page.goto(`/agents/${id}`);
    await expect(page.getByText("Private", { exact: true })).toHaveCount(0);
    for (const threadId of [id, createdId]) {
      const denied = await page.request.get(
        `/dashboard/api/threads/${threadId}`,
      );
      expect(denied.status()).toBe(404);
    }
  } finally {
    if (createdId) await api.delete(`/threads/${createdId}`);
    await api.delete(`/threads/${id}`);
    await api.dispose();
  }
});
