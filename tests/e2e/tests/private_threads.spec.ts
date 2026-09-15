import { expect, test } from "@playwright/test";
import { dismissOnboardingIfShown } from "./helpers/dashboard";

const harness = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;

test("private threads are owner-only and visibility is fixed at creation", async ({
  page,
  playwright,
}) => {
  const api = await playwright.request.newContext({ baseURL: harness });
  const sharedId = crypto.randomUUID();
  let createdId: string | undefined;
  let continuedId: string | undefined;
  try {
    const seed = await api.post("/threads", {
      data: {
        thread_id: sharedId,
        metadata: {
          title: "Shared project planning",
          source: "dashboard",
          owner_login: "alice",
          visibility: "public",
          graph_id: "agent",
        },
      },
    });
    expect(seed.ok()).toBeTruthy();
    // A real transcript: the continuation has to copy it into a thread that
    // has never run, which is the path a fresh thread without a graph rejects.
    const seeded = await api.post(`/threads/${sharedId}/state`, {
      data: {
        values: {
          messages: [
            { type: "human", content: "Shared planning notes" },
            { type: "ai", content: "Here is a plan." },
          ],
        },
      },
    });
    expect(seeded.ok()).toBeTruthy();

    // Alice starts a new thread; the composer defaults to private and the
    // server records an immutable owner.
    await page.request.post("/control/login", { data: { login: "alice" } });
    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    await expect(
      page.getByRole("button", { name: "Thread visibility" }),
    ).toHaveText(/Private/);
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
    await page.getByRole("button", { name: "Thread visibility" }).click();
    await expect(
      page.getByRole("menuitemradio", { name: "Private" }),
    ).toHaveAttribute("aria-checked", "true");
    await expect(
      page.getByRole("menuitemradio", { name: "Workspace" }),
    ).toBeDisabled();
    await page.keyboard.press("Escape");

    // Visibility cannot be changed in place; the PATCH only knows titles.
    const flip = await page.request.patch(
      `/dashboard/api/threads/${createdId}`,
      {
        headers: { origin: new URL(page.url()).origin },
        data: { visibility: "public" },
      },
    );
    expect(flip.status()).toBe(422);

    // A shared thread continues privately as a new thread; the source is untouched.
    await page.goto(`/agents/${sharedId}`);
    await page.getByRole("button", { name: "Thread visibility" }).click();
    await page.getByRole("menuitemradio", { name: "Private" }).click();
    await expect(page).toHaveURL(new RegExp(`/agents/(?!${sharedId})[^/]+$`));
    continuedId = new URL(page.url()).pathname.split("/").pop()!;
    const continued = await (
      await page.request.get(`/dashboard/api/threads/${continuedId}`)
    ).json();
    expect(continued.visibility).toBe("private");
    expect(continued.continuedFromThreadId).toBe(sharedId);
    const copied = (
      await (await api.get(`/threads/${continuedId}/state`)).json()
    ).values.messages;
    expect(copied.map((m: { content: string }) => m.content)).toEqual([
      "Shared planning notes",
      "Here is a plan.",
    ]);
    expect(
      copied.every(
        (m: { additional_kwargs?: Record<string, unknown> }) =>
          m.additional_kwargs?.collaborative_origin_thread_id === sharedId,
      ),
    ).toBe(true);
    const source = await (
      await page.request.get(`/dashboard/api/threads/${sharedId}`)
    ).json();
    expect(source.visibility).toBe("public");

    // Bob can still read the shared thread but neither private one.
    await page.request.post("/control/login", { data: { login: "bob" } });
    await page.goto(`/agents/${sharedId}`);
    await expect(
      page.getByText("Shared project planning", { exact: true }).first(),
    ).toBeVisible();
    for (const threadId of [createdId, continuedId]) {
      const denied = await page.request.get(
        `/dashboard/api/threads/${threadId}`,
      );
      expect(denied.status()).toBe(404);
    }
  } finally {
    for (const threadId of [createdId, continuedId, sharedId]) {
      if (threadId) await api.delete(`/threads/${threadId}`);
    }
    await api.dispose();
  }
});
