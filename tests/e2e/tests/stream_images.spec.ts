import { test, expect, type Page } from "@playwright/test";
import {
  SAME_ORIGIN_HEADERS,
  SAME_USER,
  loginAs,
  setThreadView,
  waitForThreadIdle,
} from "./helpers/dashboard";

type Block = { type?: string; url?: string; base64?: string };

/** Each image of the thread's state as the stream view reads it. */
async function stateImages(page: Page, threadId: string): Promise<Block[]> {
  const response = await page.request.get(
    `/dashboard/api/threads/${threadId}/state`,
  );
  if (!response.ok()) return [];
  const state = (await response.json()) as {
    values?: { messages?: Array<{ content?: unknown }> };
  };
  return (state.values?.messages ?? [])
    .flatMap((message) =>
      Array.isArray(message.content) ? (message.content as Block[]) : [],
    )
    .filter((block) => block.type === "image");
}

// A person's photo and a tool's screenshots: the state the stream view reads
// links each to the copy the transcript stored rather than carrying its bytes,
// and the page still shows every one.
test("a thread's images reach the stream view as links and still show", async ({
  page,
}) => {
  await loginAs(page, SAME_USER);
  await page.goto("/agents");
  const photo = (await page.screenshot()).toString("base64");
  const threadId = crypto.randomUUID();
  const started = await page.request.post(
    `/dashboard/api/threads/${threadId}/commands`,
    {
      headers: SAME_ORIGIN_HEADERS,
      data: {
        id: 1,
        method: "run.start",
        params: {
          assistant_id: "agent",
          input: {
            messages: [
              {
                type: "human",
                id: crypto.randomUUID(),
                content: [
                  {
                    type: "image",
                    base64: photo,
                    mime_type: "image/png",
                    file_name: "home.png",
                  },
                  {
                    type: "text",
                    text: "E2E_SCREENSHOTS Screenshot the pages.",
                  },
                ],
              },
            ],
          },
          config: {
            configurable: {
              thread_id: threadId,
              repo_explicitly_none: true,
              thread_type: "workspace",
            },
          },
        },
      },
    },
  );
  expect(started.ok(), await started.text()).toBeTruthy();

  const links = `/dashboard/api/threads/${threadId}/transcript/attachments/`;
  await expect
    .poll(
      async () =>
        (await stateImages(page, threadId)).map((image) =>
          image.url?.startsWith(links) && !image.base64 ? "link" : "inline",
        ),
      { timeout: 60_000, intervals: [1_000] },
    )
    .toEqual(Array(6).fill("link"));
  await waitForThreadIdle(page, threadId);

  await setThreadView(page, "stream");
  await page.goto(`/agents/${threadId}`);
  const shown = page.getByRole("main").locator("img[src^='blob:']");
  await expect(shown).toHaveCount(6);
  for (const image of await shown.all()) {
    await expect
      .poll(() =>
        image.evaluate((element: HTMLImageElement) => element.naturalWidth),
      )
      .toBeGreaterThan(0);
  }
});
