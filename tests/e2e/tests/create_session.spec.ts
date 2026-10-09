import { execFile } from "node:child_process";
import { resolve } from "node:path";
import { promisify } from "node:util";
import { expect, test } from "@playwright/test";
import { SAME_USER, dismissOnboardingIfShown, loginAs } from "./helpers/web";

const exec = promisify(execFile);
const root = resolve(__dirname, "../../..");
const harness = `http://127.0.0.1:${process.env.E2E_PORT ?? 2024}`;

test.beforeAll(async () => {
  await exec(
    "pnpm",
    ["install", "--frozen-lockfile", "--filter", "open-swe-cli..."],
    { cwd: root, timeout: 60_000 },
  );
  await exec("pnpm", ["--filter", "open-swe-bridge-client", "build"], {
    cwd: root,
    timeout: 60_000,
  });
});

for (const start of [undefined, false]) {
  test(`MCP create_session ${start === false ? "creates an idle thread" : "starts the agent by default"}`, async ({
    page,
    context,
    playwright,
  }) => {
    await loginAs(page, SAME_USER);
    const session = (await context.cookies()).find(
      (cookie) => cookie.name === "osw_session",
    );
    expect(session).toBeDefined();
    const api = await playwright.request.newContext({ baseURL: harness });
    let threadId: string | undefined;
    const prompt = `MCP fresh session ${start === false ? "idle" : "running"} E2E_BUSY_HOLD:30`;
    try {
      const { stdout } = await exec(
        process.execPath,
        [
          "--experimental-transform-types",
          "--input-type=module",
          "-e",
          `import { toolCommand } from ${JSON.stringify(resolve(root, "cli/src/commands.ts"))}; process.exitCode = await toolCommand(process.argv.slice(1), "e2e");`,
          "create_session",
          "--json",
          JSON.stringify({
            prompt,
            repo: "fakeorg/demo",
            workspace: "default",
            visibility: "private",
            ...(start === false ? { start } : {}),
          }),
        ],
        {
          cwd: root,
          env: {
            ...process.env,
            OPEN_SWE_BACKEND_URL: harness,
            OPEN_SWE_SESSION: session?.value,
            OPEN_SWE_API_KEY: "",
            ACTIONS_ID_TOKEN_REQUEST_URL: "",
            ACTIONS_ID_TOKEN_REQUEST_TOKEN: "",
          },
          timeout: 30_000,
        },
      );
      const result = JSON.parse(stdout) as { thread_id: string; url: string };
      threadId = result.thread_id;
      expect(result.url).toBe(`${harness}/agents/${threadId}`);
      const thread = await (await api.get(`/threads/${threadId}`)).json();
      expect(thread.metadata).toMatchObject({
        owner_login: SAME_USER.login,
        repo_owner: "fakeorg",
        repo_name: "demo",
        workspace: "default",
        visibility: "private",
      });
      await page.goto(`/agents/${threadId}`);
      await dismissOnboardingIfShown(page);
      await expect(page.getByTestId("composer-editor")).toBeVisible();
      if (start === false) {
        expect(
          await (await api.get(`/threads/${threadId}/runs`)).json(),
        ).toEqual([]);
        await expect(page.getByTestId("user-message")).toHaveCount(0);
        await expect(page.getByText("Send the first message")).toBeVisible();
      } else {
        await expect(
          page.getByTestId("user-message").filter({ hasText: prompt }),
        ).toBeVisible();
        await expect
          .poll(async () => {
            const runs = (await (
              await api.get(`/threads/${threadId}/runs`)
            ).json()) as Array<{ status: string }>;
            return runs.some(
              (run) => run.status === "running" || run.status === "success",
            );
          })
          .toBe(true);
      }
    } finally {
      if (threadId) {
        await page.request.post(`/api/threads/${threadId}/cancel`, {
          headers: { origin: harness },
        });
        await api.delete(`/threads/${threadId}`);
      }
      await api.dispose();
    }
  });
}
