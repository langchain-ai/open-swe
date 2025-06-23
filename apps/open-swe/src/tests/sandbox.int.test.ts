/* eslint-disable no-console */
import { test, expect } from "@jest/globals";
import { daytonaClient } from "../utils/sandbox.js";
import { SANDBOX_ROOT_DIR } from "@open-swe/shared/constants";
import { DEFAULT_SANDBOX_CREATE_PARAMS } from "../constants.js";

test.skip("Can execute rg commands", async () => {
  const githubToken = process.env.GITHUB_PAT;
  if (!githubToken) {
    throw new Error("GITHUB_PAT environment variable is not set");
  }

  const client = daytonaClient();

  console.log("Setting up sandbox...");
  const sandbox = await client.create(DEFAULT_SANDBOX_CREATE_PARAMS);
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

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

test("Installing dependencies", async () => {
  const githubToken = process.env.GITHUB_PAT;
  if (!githubToken) {
    throw new Error("GITHUB_PAT environment variable is not set");
  }

  const client = daytonaClient();

  console.log("Setting up sandbox...");
  const sandbox = await client.create(DEFAULT_SANDBOX_CREATE_PARAMS);
  console.log("Setup sandbox:", sandbox.id);

  try {
    const repoUrlWithToken = `https://x-access-token:${githubToken}@github.com/langchain-ai/open-swe.git`;
    const cloneCommand = `git clone ${repoUrlWithToken}`;

    console.log("Cloning repo...");
    const cloneRes = await sandbox.process.executeCommand(
      cloneCommand,
      SANDBOX_ROOT_DIR,
    );
    expect(cloneRes.exitCode).toBe(0);

    const installCommand = "yarn install";
    const installRes = await sandbox.process.executeCommand(
      installCommand,
      `${SANDBOX_ROOT_DIR}/open-swe`,
      DEFAULT_ENV,
      120, // 120 seconds timeout
    );
    console.log(
      `install res status: ${installRes.exitCode}\ninstall res output: ${installRes.result}`,
    );
    console.log(
      `Install res exit code: ${installRes.exitCode}\nInstall res output: ${installRes.result}`,
    );
    expect(installRes.exitCode).toBe(0);
  } finally {
    await sandbox.delete();
    console.log("Deleted sandbox:", sandbox.id);
  }
}, 300_000); // 5 minutes timeout
