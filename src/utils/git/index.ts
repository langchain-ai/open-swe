import { CommandResult, Sandbox } from "@e2b/code-interpreter";
import { GraphConfig } from "../../types.js";
import { TIMEOUT_MS } from "../../constants.js";

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
  options?: {
    isNew?: boolean;
  },
): Promise<CommandResult | false> {
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
        if (options?.isNew) {
          console.warn(
            `Branch '${branchName}' already exists and is the current branch. Cannot create new branch with the same name.`,
          );
          return {
            stdout: "",
            stderr: `fatal: A branch named '${branchName}' already exists.`,
            exitCode: 128,
          };
        }
        console.log(`Already on branch '${branchName}'. No checkout needed.`);
        return {
          stdout: `Already on branch ${branchName}`,
          stderr: "",
          exitCode: 0,
        };
      }
    }

    const gitCheckoutOutput = await sandbox.commands.run(
      `git checkout ${options?.isNew ? "-b" : ""} "${branchName}"`,
      { cwd: absoluteRepoDir },
    );

    if (gitCheckoutOutput.exitCode !== 0) {
      console.error("Failed to checkout branch", gitCheckoutOutput);
      return false;
    }

    return gitCheckoutOutput;
  } catch (e) {
    console.error("Failed to checkout branch", e);
    return false;
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

  return gitStatusOutput.stdout.split("\n").map((line) => line.trim());
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

  console.log(`Checking out branch ${branchName}`);
  await checkoutBranch(absoluteRepoDir, branchName, sandbox);

  console.log(`Committing changes to branch ${branchName}`);
  await commitAllAndPush(absoluteRepoDir, "Apply patch", sandbox);
  console.log("Successfully checked out & committed changes.\n");

  return branchName;
}
