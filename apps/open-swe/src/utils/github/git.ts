import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../logger.js";
import { GraphConfig, TargetRepository } from "@open-swe/shared/open-swe/types";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getSandboxErrorFields } from "../sandbox-error-fields.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { ExecuteResponse } from "@daytonaio/sdk/src/types/ExecuteResponse.js";
import { withRetry } from "../retry.js";

const logger = createLogger(LogLevel.INFO, "GitHub-Git");

export function getBranchName(configOrThreadId: GraphConfig | string): string {
  const threadId =
    typeof configOrThreadId === "string"
      ? configOrThreadId
      : configOrThreadId.configurable?.thread_id;
  if (!threadId) {
    throw new Error("No thread ID provided");
  }

  return `open-swe/${threadId}`;
}

export async function getChangedFilesStatus(
  absoluteRepoDir: string,
  sandbox: Sandbox,
): Promise<string[]> {
  const gitStatusOutput = await sandbox.process.executeCommand(
    "git status --porcelain",
    absoluteRepoDir,
    undefined,
    TIMEOUT_SEC,
  );

  if (gitStatusOutput.exitCode !== 0) {
    logger.error(`Failed to get changed files status`, {
      gitStatusOutput,
    });
    return [];
  }

  return gitStatusOutput.result
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

export async function stashAndClearChanges(
  absoluteRepoDir: string,
  sandbox: Sandbox,
): Promise<ExecuteResponse | false> {
  try {
    const gitStashOutput = await sandbox.process.executeCommand(
      "git add -A && git stash && git reset --hard",
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (gitStashOutput.exitCode !== 0) {
      logger.error(`Failed to stash and clear changes`, {
        gitStashOutput,
      });
    }
    return gitStashOutput;
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error(`Failed to stash and clear changes`, {
      ...(errorFields && { errorFields }),
      ...(e instanceof Error && {
        name: e.name,
        message: e.message,
        stack: e.stack,
      }),
    });
    return errorFields ?? false;
  }
}

export async function checkoutBranchAndCommit(
  config: GraphConfig,
  targetRepository: TargetRepository,
  sandbox: Sandbox,
  options: {
    branchName?: string;
    githubInstallationToken: string;
  },
): Promise<{ branchName: string; pullRequestNumber?: number; }> {
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
  const branchName = options.branchName || getBranchName(config);

  logger.info(`Committing changes to branch ${branchName}`);
  // Commit the changes. We can use the sandbox executeCommand API for this since it doesn't require a token.
  await sandbox.git.add(absoluteRepoDir, ["."]);

  const botAppName = process.env.GITHUB_APP_NAME;
  if (!botAppName) {
    logger.error("GITHUB_APP_NAME environment variable is not set.");
    throw new Error("GITHUB_APP_NAME environment variable is not set.");
  }
  const userName = `${botAppName}[bot]`;
  const userEmail = `${botAppName}@users.noreply.github.com`;
  await sandbox.git.commit(absoluteRepoDir, "Apply patch", userName, userEmail);

  // Push the changes using the git API so it handles authentication for us.
  const pushRes = await withRetry(
    async () => {
      return await sandbox.git.push(
        absoluteRepoDir,
        "git",
        options.githubInstallationToken,
      );
    },
    { retries: 3, delay: 0 },
  );

  if (pushRes instanceof Error) {
    const errorFields = {
      ...(pushRes instanceof Error
        ? {
            name: pushRes.name,
            message: pushRes.message,
            stack: pushRes.stack,
            cause: pushRes.cause,
          }
        : pushRes),
    };
    logger.error("Failed to push changes", errorFields);
    throw new Error("Failed to push changes");
  }

  // Check if this is the first commit by checking if pullRequestNumbers is empty
  const { pullRequestNumbers } = config.state;
  let pullRequestNumber;
  if (!pullRequestNumbers || pullRequestNumbers.length === 0) {
    logger.info("First commit detected, creating a draft pull request.");
    const pullRequest = await createPullRequest({
      owner: targetRepository.owner,
      repo: targetRepository.repo,
      headBranch: branchName,
      title: "Initial draft PR",
      githubInstallationToken: options.githubInstallationToken,
      draft: true,
    });
    if (pullRequest) {
      pullRequestNumber = pullRequest.number;
      logger.info(`Draft pull request created: #${pullRequestNumber}`);
    }
  }

  logger.info("Successfully checked out & committed changes.", {
    commitAuthor: userName,
  });

  return { branchName, pullRequestNumber };
}

export async function pullLatestChanges(
  absoluteRepoDir: string,
  sandbox: Sandbox,
  args: {
    githubInstallationToken: string;
  },
): Promise<boolean> {
  try {
    await sandbox.git.pull(
      absoluteRepoDir,
      "git",
      args.githubInstallationToken,
    );
    return true;
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error(`Failed to pull latest changes`, {
      ...(errorFields && { errorFields }),
      ...(e instanceof Error && {
        name: e.name,
        message: e.message,
        stack: e.stack,
      }),
    });
    return false;
  }
}

/**
 * Securely clones a GitHub repository using temporary credential helper.
 * The GitHub installation token is never persisted in the Git configuration or remote URLs.
 */
export async function cloneRepo(
  sandbox: Sandbox,
  targetRepository: TargetRepository,
  args: {
    githubInstallationToken: string;
    stateBranchName?: string;
  },
): Promise<string> {
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
  const cloneUrl = `https://github.com/${targetRepository.owner}/${targetRepository.repo}.git`;
  const branchName = args.stateBranchName || targetRepository.branch;

  try {
    // Attempt to clone the repository
    return await performClone(sandbox, cloneUrl, {
      branchName,
      targetRepository,
      absoluteRepoDir,
      githubInstallationToken: args.githubInstallationToken,
    });
  } catch (error) {
    const errorFields = getSandboxErrorFields(error);
    logger.error("Clone repo failed", errorFields ?? error);
    throw error;
  }
}

/**
 * Performs the actual Git clone operation, handling branch-specific logic.
 * Returns the branch name that was cloned.
 */
async function performClone(
  sandbox: Sandbox,
  cloneUrl: string,
  args: {
    branchName: string | undefined;
    targetRepository: TargetRepository;
    absoluteRepoDir: string;
    githubInstallationToken: string;
  },
): Promise<string> {
  const {
    branchName,
    targetRepository,
    absoluteRepoDir,
    githubInstallationToken,
  } = args;
  logger.info("Cloning repository", {
    repoPath: `${targetRepository.owner}/${targetRepository.repo}`,
    branch: branchName,
    baseCommit: targetRepository.baseCommit,
  });

  await sandbox.git.clone(
    cloneUrl,
    absoluteRepoDir,
    targetRepository.branch,
    targetRepository.baseCommit,
    "git",
    githubInstallationToken,
  );

  logger.info("Successfully cloned repository", {
    repoPath: `${targetRepository.owner}/${targetRepository.repo}`,
    branch: branchName,
    baseCommit: targetRepository.baseCommit,
  });

  if (targetRepository.baseCommit) {
    return targetRepository.baseCommit;
  }

  if (!branchName) {
    throw new Error(
      "Can not create new branch or checkout existing branch without branch name",
    );
  }

  try {
    await sandbox.git.createBranch(absoluteRepoDir, branchName);
    logger.info("Created branch", {
      branch: branchName,
    });

    const setUpstreamBranchRes = await sandbox.process.executeCommand(
      `git branch --set-upstream-to=origin/${branchName}`,
      absoluteRepoDir,
    );
    if (setUpstreamBranchRes.exitCode !== 0) {
      logger.error("Failed to set upstream branch", {
        setUpstreamBranchRes,
      });
    }
    logger.info("Set upstream branch");

    return branchName;
  } catch {
    logger.info("Failed to create branch, checking out branch", {
      branch: branchName,
    });
  }

  await sandbox.git.checkoutBranch(absoluteRepoDir, branchName);
  logger.info("Checked out branch", {
    branch: branchName,
  });

  const setUpstreamBranchRes = await sandbox.process.executeCommand(
    `git branch --set-upstream-to=origin/${branchName}`,
    absoluteRepoDir,
  );
  if (setUpstreamBranchRes.exitCode !== 0) {
    logger.error("Failed to set upstream branch", {
      setUpstreamBranchRes,
    });
  }
  logger.info("Set upstream branch");

  return branchName;
}

