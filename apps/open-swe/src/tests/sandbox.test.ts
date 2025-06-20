import { test, expect } from "@jest/globals";
import { daytonaClient } from "../utils/sandbox.js";
import { SNAPSHOT_NAME } from "@open-swe/shared/constants";
// import { getRepoAbsolutePath } from "@open-swe/shared/git";

test("Can execute rg commands", async () => {
  const githubToken = process.env.GITHUB_PAT;
  if (!githubToken) {
    throw new Error("GITHUB_PAT environment variable is not set");
  }

  const client = daytonaClient();

  console.log("Setting up sandbox...");
  const sandbox = await client.create({ image: SNAPSHOT_NAME });
  // const sandbox = await client.get("0f0e878d-983c-4d26-9d58-41e63640150f")
  console.log("Setup sandbox:", sandbox.id);

  // try {
  const repoUrlWithToken = `https://x-access-token:${githubToken}@github.com/langchain-ai/open-swe.git`;
  const cloneCommand = `git clone ${repoUrlWithToken}`;

  console.log("Cloning repo...");
  const cloneRes = await sandbox.process.executeCommand(
    cloneCommand,
    "/home/daytona",
  );
  expect(cloneRes.exitCode).toBe(0);

  const testRes = await sandbox.process.executeCommand(
    "rg --version && rg -i logger",
    "/home/daytona",
  );
  console.log(
    `test res status: ${testRes.exitCode}\ntest res output: ${testRes.result}`,
  );

  expect(testRes.exitCode).toBe(0);

  //   const repoRoot = getRepoAbsolutePath({ owner: "langchain-ai", repo: "open-swe" });
  //   const rgCommand = `cd ${repoRoot} && ls && rg --version`;

  //   console.log("Running rg command...")
  //   const rgCommandRes = await sandbox.process.executeCommand(rgCommand);
  //   console.log(`RG STATUS: ${rgCommandRes.exitCode}`)

  //   const wgetCommand = `wget https://github.com/BurntSushi/ripgrep/releases/download/14.1.1/ripgrep_14.1.1-1_amd64.deb`;
  //   console.log("Running wget command...")
  //   const wgetCommandRes = await sandbox.process.executeCommand(wgetCommand);
  //   console.log(`WGET STATUS: ${wgetCommandRes.exitCode}`)

  //   const dpkgCommand = `sudo dpkg -i ripgrep_14.1.1-1_amd64.deb`;
  //   console.log("Running dpkg command...")
  //   const dpkgCommandRes = await sandbox.process.executeCommand(dpkgCommand);
  //   console.log(`DPKG STATUS: ${dpkgCommandRes.exitCode}`)

  //   const rgCommand2 = `rg --version`;
  //   console.log("Running rg command...")
  //   const rgCommandRes2 = await sandbox.process.executeCommand(rgCommand2);
  //   console.log(`RG VERSION STATUS: ${rgCommandRes2.exitCode}`)

  //   const actualRgCommand = `rg -i logger`;
  //   console.log("Running actual rg command...")
  //   const actualRgCommandRes = await sandbox.process.executeCommand(actualRgCommand);
  //   console.log(`ACTUAL RG STATUS: ${actualRgCommandRes.exitCode}\nOUTPUT: ${actualRgCommandRes.result}`);

  //   expect(actualRgCommandRes.exitCode).toBe(0);
  // } finally {
  //   // await sandbox.delete();
  //   console.log("Deleted sandbox:", sandbox.id);
  // }
});
