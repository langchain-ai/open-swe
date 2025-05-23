import { CommandResult, Sandbox } from "@e2b/code-interpreter";
import { GraphConfig } from "../../types.js";
import { TIMEOUT_MS } from "../../constants.js";
import { getSandboxErrorFields } from "../sandbox-error-fields.js";

export function getRepoAbsolutePath(config: GraphConfig): string {
  const repoName = config.configurable?.target_repository.repo;
  if (!repoName) {
    throw new Error("No repository name provided");
  }

  return `/home/user/${repoName}`;
}

export function getBranchName(config: GraphConfig): string {
  const threadId = config.configurable?.thread_id;
  if (!threadId) {
    throw new Error("No thread ID provided");
  }

  return `open-swe/${threadId}`;
}

export async function checkoutBranch(
  absoluteRepoDir: string,
  branchName: string,
  sandbox: Sandbox,
): Promise<CommandResult | false> {
  console.log("\nChecking out branch...", {
    branchName,
  });

  try {
    const getCurrentBranchOutput = await sandbox.commands.run(
      "git branch --show-current",
      { cwd: absoluteRepoDir },
    );
    await sandbox.setTimeout(TIMEOUT_MS);

    if (getCurrentBranchOutput.exitCode !== 0) {
      console.error("Failed to get current branch", getCurrentBranchOutput);
    } else {
      const currentBranch = getCurrentBranchOutput.stdout.trim();
      if (currentBranch === branchName) {
        console.log(`\nAlready on branch '${branchName}'. No checkout needed.`);
        return {
          stdout: `Already on branch ${branchName}`,
          stderr: "",
          exitCode: 0,
        };
      }
    }
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    console.error("Failed to get current branch", errorFields ?? e);
    return false;
  }

  let checkoutCommand: string;
  try {
    console.log("\nChecking if branch exists...", {
      command: `git rev-parse --verify --quiet "refs/heads/${branchName}"`,
    });
    // Check if branch exists using git rev-parse for robustness
    const checkBranchExistsOutput = await sandbox.commands.run(
      `git rev-parse --verify --quiet "refs/heads/${branchName}"`,
      { cwd: absoluteRepoDir },
    );
    await sandbox.setTimeout(TIMEOUT_MS);

    if (checkBranchExistsOutput.exitCode === 0) {
      // Branch exists (rev-parse exit code 0 means success)
      checkoutCommand = `git checkout "${branchName}"`;
    } else {
      // Branch does not exist (rev-parse non-zero exit code) or other error.
      // Attempt to create it.
      checkoutCommand = `git checkout -b "${branchName}"`;
    }
  } catch (e: unknown) {
    const errorFields = getSandboxErrorFields(e);
    if (
      errorFields &&
      errorFields.exitCode === 1 &&
      errorFields.stderr === ""
    ) {
      checkoutCommand = `git checkout -b "${branchName}"`;
    } else {
      console.error("\nError checking if branch exists", e);
      return false;
    }
  }

  try {
    const gitCheckoutOutput = await sandbox.commands.run(checkoutCommand, {
      cwd: absoluteRepoDir,
    });

    if (gitCheckoutOutput.exitCode !== 0) {
      console.error("\nFailed to checkout branch", gitCheckoutOutput);
      return false;
    }

    console.log("\nChecked out branch successfully.", {
      branchName,
      gitCheckoutOutput: gitCheckoutOutput.stdout,
    });

    return gitCheckoutOutput;
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    console.error("Error checking out branch", errorFields ?? e);
    return false;
  }
}

interface GitHubUserResponse {
  login: string;
  id: number;
  name: string | null;
  email: string | null;
}

async function getGitUserDetailsFromGitHub(): Promise<{
  userName?: string;
  userEmail?: string;
}> {
  const githubToken = process.env.GITHUB_PAT;
  if (!githubToken) {
    console.warn(
      "GITHUB_PAT environment variable is not set. Cannot fetch user details from GitHub.",
    );
    return {};
  }

  try {
    const response = await fetch("https://api.github.com/user", {
      headers: {
        Authorization: `token ${githubToken}`,
        Accept: "application/vnd.github.v3+json",
      },
    });

    if (!response.ok) {
      console.error(
        `Failed to fetch GitHub user info: ${response.status} ${response.statusText}. Response: ${await response.text()}`,
      );
      return {};
    }

    const userData = (await response.json()) as GitHubUserResponse;
    const fetchedUserName = userData.name || userData.login;
    let fetchedUserEmail = userData.email; // This can be string | null

    if (!fetchedUserEmail && userData.id && userData.login) {
      fetchedUserEmail = `${userData.id}+${userData.login}@users.noreply.github.com`;
    } else if (!fetchedUserEmail && userData.login) {
      fetchedUserEmail = `${userData.login}@users.noreply.github.com`;
    }

    const finalUserName = fetchedUserName || undefined;
    const finalUserEmail = fetchedUserEmail || undefined;

    if (!finalUserName) {
      console.warn("Could not determine GitHub username from API response.");
    }
    if (!finalUserEmail) {
      console.warn("Could not determine GitHub user email from API response.");
    }
    return { userName: finalUserName, userEmail: finalUserEmail };
  } catch (e) {
    console.error("Error fetching GitHub user info:", e);
    return {};
  }
}

export async function configureGitUserInRepo(
  absoluteRepoDir: string,
  sandbox: Sandbox,
): Promise<void> {
  let needsGitConfig = false;
  try {
    const nameCheck = await sandbox.commands.run("git config user.name", {
      cwd: absoluteRepoDir,
    });
    await sandbox.setTimeout(TIMEOUT_MS);
    const emailCheck = await sandbox.commands.run("git config user.email", {
      cwd: absoluteRepoDir,
    });
    await sandbox.setTimeout(TIMEOUT_MS);

    if (
      nameCheck.exitCode !== 0 ||
      nameCheck.stdout.trim() === "" ||
      emailCheck.exitCode !== 0 ||
      emailCheck.stdout.trim() === ""
    ) {
      needsGitConfig = true;
    }
  } catch (checkError) {
    console.warn(
      "Could not check existing git config, will attempt to set it:",
      checkError,
    );
    needsGitConfig = true;
  }

  if (needsGitConfig) {
    const { userName, userEmail } = await getGitUserDetailsFromGitHub();

    if (userName) {
      const configUserNameOutput = await sandbox.commands.run(
        `git config user.name "${userName}"`,
        { cwd: absoluteRepoDir },
      );
      await sandbox.setTimeout(TIMEOUT_MS);
      if (configUserNameOutput.exitCode !== 0) {
        console.error(
          "Failed to set git user.name:",
          configUserNameOutput.stderr || configUserNameOutput.stdout,
        );
      } else {
        console.log("\nSet git user.name successfully.", {
          userName,
        });
      }
    }

    if (userEmail) {
      const configUserEmailOutput = await sandbox.commands.run(
        `git config user.email "${userEmail}"`,
        { cwd: absoluteRepoDir },
      );
      await sandbox.setTimeout(TIMEOUT_MS);
      if (configUserEmailOutput.exitCode !== 0) {
        console.error(
          "Failed to set git user.email:",
          configUserEmailOutput.stderr || configUserEmailOutput.stdout,
        );
      } else {
        console.log("\nSet git user.email successfully.", {
          userEmail,
        });
      }
    }
  } else {
    console.log(
      "Git user.name and user.email are already configured in this repository.",
    );
  }
}

export async function commitAll(
  absoluteRepoDir: string,
  message: string,
  sandbox: Sandbox,
): Promise<CommandResult | false> {
  try {
    const gitAddOutput = await sandbox.commands.run(
      `git add -A && git commit -m "${message}"`,
      { cwd: absoluteRepoDir },
    );
    await sandbox.setTimeout(TIMEOUT_MS);

    if (gitAddOutput.exitCode !== 0) {
      console.error(
        "Failed to commit all changes to git repository",
        gitAddOutput,
      );
    }
    return gitAddOutput;
  } catch (e) {
    console.error("Failed to commit all changes to git repository", e);
    return false;
  }
}

export async function commitAllAndPush(
  absoluteRepoDir: string,
  message: string,
  sandbox: Sandbox,
): Promise<CommandResult | false> {
  try {
    const commitOutput = await commitAll(absoluteRepoDir, message, sandbox);

    const pushCurrentBranchCmd =
      "git push -u origin $(git rev-parse --abbrev-ref HEAD)";

    if (!commitOutput || commitOutput.exitCode !== 0) {
      return false;
    }

    const gitPushOutput = await sandbox.commands.run(pushCurrentBranchCmd, {
      cwd: absoluteRepoDir,
    });
    await sandbox.setTimeout(TIMEOUT_MS);

    if (gitPushOutput.exitCode !== 0) {
      console.error("Failed to push changes to git repository", gitPushOutput);
      return false;
    }

    return gitPushOutput;
  } catch (e) {
    console.error("Failed to commit all and push changes to git repository", e);
    return false;
  }
}

export async function getChangedFilesStatus(
  absoluteRepoDir: string,
  sandbox: Sandbox,
): Promise<string[]> {
  const gitStatusOutput = await sandbox.commands.run("git status --porcelain", {
    cwd: absoluteRepoDir,
  });

  if (gitStatusOutput.exitCode !== 0) {
    console.error("Failed to get changed files status", gitStatusOutput);
    return [];
  }

  return gitStatusOutput.stdout
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

export async function checkoutBranchAndCommit(
  config: GraphConfig,
  sandbox: Sandbox,
  options?: {
    branchName?: string;
  },
): Promise<string> {
  console.log("\nChecking out branch and committing changes...");
  const absoluteRepoDir = getRepoAbsolutePath(config);
  const branchName = options?.branchName || getBranchName(config);

  await checkoutBranch(absoluteRepoDir, branchName, sandbox);

  console.log(`Committing changes to branch ${branchName}`);
  await commitAllAndPush(absoluteRepoDir, "Apply patch", sandbox);
  console.log("Successfully checked out & committed changes.\n");

  return branchName;
}
