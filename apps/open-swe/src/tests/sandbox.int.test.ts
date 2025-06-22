/* eslint-disable no-console */
import { test, expect } from "@jest/globals";
import { daytonaClient } from "../utils/sandbox.js";
import { SNAPSHOT_NAME } from "@open-swe/shared/constants";

test("Can execute rg commands", async () => {
  const githubToken = process.env.GITHUB_PAT;
  if (!githubToken) {
    throw new Error("GITHUB_PAT environment variable is not set");
  }

  const client = daytonaClient();

  console.log("Setting up sandbox...");
  const sandbox = await client.create({
    image: SNAPSHOT_NAME,
    user: "daytona",
  });
  console.log("Setup sandbox:", sandbox.id);

  const repoUrlWithToken = `https://x-access-token:${githubToken}@github.com/langchain-ai/open-swe.git`;
  const cloneCommand = `git clone ${repoUrlWithToken}`;

  console.log("Cloning repo...");
  const cloneRes = await sandbox.process.executeCommand(
    cloneCommand,
    "/home/daytona",
  );
  expect(cloneRes.exitCode).toBe(0);

  const testRes = await sandbox.process.executeCommand(
    `script --return --quiet -c "$(cat <<'OPEN_SWE_X'
rg -i logger
OPEN_SWE_X
)" /dev/null`,
    "/home/daytona/open-swe",
  );
  console.log(
    `test res status: ${testRes.exitCode}\ntest res output: ${testRes.result}`,
  );

  expect(testRes.exitCode).toBe(0);
});
