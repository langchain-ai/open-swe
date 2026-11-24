import { test, expect, type Page } from "@playwright/test";
import {
  SAME_ORIGIN_HEADERS,
  SAME_USER,
  loginAs,
  waitForThreadIdle,
} from "./helpers/dashboard";

type StateMessage = {
  type?: string;
  tool_call_id?: string;
  artifact?: { checkpoint_ns?: string } | null;
};

/** Each finished `task` call's id, with the namespace its result records. */
async function taskNamespaces(
  page: Page,
  threadId: string,
): Promise<Record<string, string | null>> {
  const response = await page.request.get(
    `/dashboard/api/threads/${threadId}/state`,
  );
  if (!response.ok()) return {};
  const state = (await response.json()) as {
    values?: { messages?: StateMessage[] };
  };
  return Object.fromEntries(
    (state.values?.messages ?? [])
      .filter(
        (message) =>
          message.type === "tool" &&
          message.tool_call_id?.startsWith("call-subagent-"),
      )
      .map((message) => [
        message.tool_call_id,
        message.artifact?.checkpoint_ns ?? null,
      ]),
  );
}

test("a subagent's page shows the subagent's own conversation in the stream view", async ({
  page,
}) => {
  test.setTimeout(150_000);
  await loginAs(page, SAME_USER);
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
                    type: "text",
                    text: "E2E_DELEGATE investigate the repository",
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

  await expect
    .poll(() => taskNamespaces(page, threadId), {
      timeout: 90_000,
      intervals: [1_000],
    })
    .toEqual({
      "call-subagent-layout": expect.stringMatching(/^tools:/),
      "call-subagent-files": expect.stringMatching(/^tools:/),
    });
  await waitForThreadIdle(page, threadId);

  await page.addInitScript(() =>
    window.localStorage.setItem("open-swe.streaming.use-stream", "true"),
  );
  await page.goto(`/agents/${threadId}?subagent=call-subagent-layout`);
  const main = page.getByRole("main");
  await expect(
    main.getByText("Subagent finished: listed the workspace.").first(),
  ).toBeVisible();
  await expect(main.getByText("list the top-level files")).toHaveCount(0);
  await expect(
    main.getByText("Delegating the investigation to two subagents."),
  ).toHaveCount(0);
});
