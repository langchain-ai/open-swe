import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gzipSync } from "node:zlib";
import { expect, test } from "@playwright/test";
import {
  SAME_USER,
  dismissOnboardingIfShown,
  loginAs,
} from "./helpers/dashboard";

const harness = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;
const transcript = readFileSync(
  resolve(__dirname, "..", "fixtures", "claude-session.jsonl"),
  "utf8",
);

interface Block {
  type: string;
  text?: string;
  id?: string;
  name?: string;
  input?: { command?: string };
  tool_use_id?: string;
  content?: unknown;
}

interface TranscriptRecord {
  type: string;
  message?: { content: string | Block[] };
  attachment?: { type: string; prompt?: string };
}

const records = transcript
  .split("\n")
  .filter((line) => line.trim())
  .map((line) => JSON.parse(line) as TranscriptRecord);

const humanPrompts = records.flatMap((record) => {
  if (record.type === "user" && typeof record.message?.content === "string")
    return [record.message.content];
  if (
    record.type === "attachment" &&
    record.attachment?.type === "queued_command"
  )
    return [record.attachment.prompt ?? ""];
  return [];
});

const assistantBlocks = records.flatMap((record) =>
  record.type === "assistant" && Array.isArray(record.message?.content)
    ? record.message.content
    : [],
);
const assistantTexts = assistantBlocks.flatMap((block) =>
  block.type === "text" && block.text ? [block.text] : [],
);
const toolUses = assistantBlocks.filter((block) => block.type === "tool_use");
const toolResults = new Map(
  records.flatMap((record) =>
    record.type === "user" && Array.isArray(record.message?.content)
      ? record.message.content.flatMap((block) =>
          block.type === "tool_result" && typeof block.content === "string"
            ? [[block.tool_use_id ?? "", block.content] as const]
            : [],
        )
      : [],
  ),
);

function normalized(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

test("an uploaded Claude Code session opens as a thread with its whole conversation", async ({
  page,
  playwright,
}) => {
  expect(humanPrompts.length).toBeGreaterThan(1);
  expect(assistantTexts.length).toBeGreaterThan(0);
  expect(toolUses.length).toBeGreaterThan(1);

  const api = await playwright.request.newContext({ baseURL: harness });
  let threadId: string | undefined;
  try {
    await loginAs(page, SAME_USER);
    const unpushed = await api.post("/control/session-upload", {
      data: {
        login: SAME_USER.login,
        type: "claude",
        repo: "fakeorg/demo",
        branch: "never-pushed",
      },
    });
    expect(unpushed.status()).toBe(422);
    expect(await unpushed.text()).toContain("push it first");

    const seeded = await api.post("/control/pull-request", {
      data: {
        repo: "fakeorg/demo",
        head: "environment-rewrite",
        title: "Environment rewrite",
        files: { "environments.md": "setup and init scripts\n" },
      },
    });
    expect(seeded.ok(), await seeded.text()).toBeTruthy();
    const { number } = (await seeded.json()) as { number: number };

    const reserved = await api.post("/control/session-upload", {
      data: {
        login: SAME_USER.login,
        email: SAME_USER.email,
        type: "claude",
        pr_url: `https://github.com/fakeorg/demo/pull/${number}`,
        visibility: "private",
      },
    });
    expect(reserved.ok(), await reserved.text()).toBeTruthy();
    const { upload_url: uploadUrl, thread_id: reservedId } =
      (await reserved.json()) as { upload_url: string; thread_id: string };
    threadId = reservedId;

    // What the agent curls: the transcript alone, authorized by the URL.
    const uploadTranscript = () =>
      api.post(new URL(uploadUrl).pathname, {
        headers: {
          "content-type": "application/x-ndjson",
          "content-encoding": "gzip",
        },
        data: gzipSync(Buffer.from(transcript)),
      });
    const upload = await uploadTranscript();
    expect(upload.ok(), await upload.text()).toBeTruthy();
    expect((await upload.json()).id).toBe(threadId);
    expect((await uploadTranscript()).status()).toBe(409);

    const summary = await (
      await page.request.get(`/dashboard/api/threads/${threadId}`)
    ).json();
    expect(summary).toMatchObject({
      title: "Environment system rewrite",
      repoFullName: "fakeorg/demo",
      branch: "environment-rewrite",
      visibility: "private",
    });
    const thread = await (await api.get(`/threads/${threadId}`)).json();
    expect(thread.metadata.pr_number).toBe(number);

    const state = await (await api.get(`/threads/${threadId}/state`)).json();
    const messages = state.values.messages as Array<{
      type: string;
      tool_calls?: Array<{ id: string }>;
      tool_call_id?: string;
    }>;
    expect(
      messages.flatMap((message) =>
        (message.tool_calls ?? []).map((call) => call.id),
      ),
    ).toEqual(toolUses.map((use) => use.id));
    expect(
      messages
        .filter((message) => message.type === "tool")
        .map((message) => message.tool_call_id)
        .sort(),
    ).toEqual(toolUses.map((use) => use.id).sort());

    await page.goto("/agents");
    await dismissOnboardingIfShown(page);
    await page.goto(`/agents/${threadId}`);

    const people = page
      .getByTestId("user-message")
      .filter({ hasNot: page.getByTestId("system-message-toggle") });
    await expect(people).toHaveCount(humanPrompts.length);
    for (const [index, prompt] of humanPrompts.entries()) {
      await expect(people.nth(index)).toContainText(prompt);
    }
    await expect(
      page.getByRole("status").filter({ hasText: "Working" }),
    ).toHaveCount(0);

    for (const text of assistantTexts) {
      const firstSentence = text.split("\n")[0].replace(/[*`]/g, "");
      await expect(
        page.getByText(firstSentence, { exact: false }),
      ).toBeVisible();
    }

    const folds = page.getByRole("button", { name: /^Worked · \d+ actions?$/ });
    const foldLabels = await folds.allTextContents();
    expect(
      foldLabels.reduce(
        (total, label) => total + Number(/(\d+) action/.exec(label)?.[1] ?? 0),
        0,
      ),
    ).toBe(toolUses.length);
    for (let index = 0; index < foldLabels.length; index++) {
      await folds.nth(index).click();
    }
    const rows = page.getByRole("button", { name: /^Shell / });
    await expect(rows).toHaveCount(toolUses.length);
    const shown = (await rows.locator("p").allTextContents()).map((label) =>
      label.replace(/^Shell\s*/, "").replace(/\.\.\.$/, ""),
    );
    for (const [index, use] of toolUses.entries()) {
      expect(shown[index].length).toBeGreaterThan(20);
      expect(normalized(use.input?.command ?? "")).toContain(
        normalized(shown[index]),
      );
    }

    const firstOutput = toolResults.get(toolUses[0].id ?? "") ?? "";
    await rows.first().click();
    await expect(
      page
        .getByText(firstOutput.split("\n")[0].trim(), { exact: false })
        .first(),
    ).toBeVisible();
  } finally {
    if (threadId) await api.delete(`/threads/${threadId}`);
    await api.dispose();
  }
});
