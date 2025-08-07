import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../logger.js";
import {
  GraphConfig,
  TargetRepository,
  TaskPlan,
} from "@open-swe/shared/open-swe/types";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getSandboxErrorFields } from "../sandbox-error-fields.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { ExecuteResponse } from "@daytonaio/sdk/src/types/ExecuteResponse.js";
import { withRetry } from "../retry.js";
import {
  addPullRequestNumberToActiveTask,
  getActiveTask,
  getPullRequestNumberFromActiveTask,
} from "@open-swe/shared/open-swe/tasks";
import { createPullRequest, getBranch } from "./api.js";
import { addTaskPlanToIssue } from "./issue-task.js";
import { DEFAULT_EXCLUDED_PATTERNS } from "./constants.js";
import { escapeRegExp } from "../string-utils.js";
import { isLocalMode } from "@open-swe/shared/open-swe/local-mode";
import { createShellExecutor } from "../shell-executor/index.js";

const logger = createLogger(LogLevel.INFO, "GitHub-Git");

/**
 * Parses git status output and returns an array of file paths.
 * Removes the git status indicators (first 3 characters) from each line.
 */
export function parseGitStatusOutput(gitStatusOutput: string): string[] {
  return gitStatusOutput
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => line.substring(3))
    .filter(Boolean);
}

/**
 * Validates and filters files before git add operation.
 * Excludes files/directories that should not be committed.
 */
async function getValidFilesToCommit(
  absoluteRepoDir: string,
  sandbox: Sandbox,
  config: GraphConfig,
  excludePatterns: string[] = DEFAULT_EXCLUDED_PATTERNS,
): Promise<string[]> {
  // Use unified shell executor
  const executor = createShellExecutor(config);
  const gitStatusOutput = await executor.executeCommand({
    command: "git status --porcelain",
    workdir: absoluteRepoDir,
    timeout: TIMEOUT_SEC,
    sandbox,
  });

  if (gitStatusOutput.exitCode !== 0) {
    logger.error(`Failed to get git status for file validation`, {
      gitStatusOutput,
    });
    throw new Error("Failed to get git status for file validation");
  }

  const allFiles = parseGitStatusOutput(gitStatusOutput.result);

  const validFiles = allFiles.filter((filePath) => {
    return !shouldExcludeFile(filePath, excludePatterns);
  });

  const excludedFiles = allFiles.filter((filePath) => {
    return shouldExcludeFile(filePath, excludePatterns);
  });

  if (excludedFiles.length > 0) {
    logger.info(`Excluded ${excludedFiles.length} files from commit:`, {
      excludedFiles: excludedFiles,
    });
  }

  return validFiles;
}

/**
 * Checks if a file should be excluded from commits based on patterns.
 */
export function shouldExcludeFile(
  filePath: string,
  excludePatterns: string[],
): boolean {
  const normalizedPath = filePath.replace(/\\/g, "/");

  return excludePatterns.some((pattern) => {
    if (pattern.includes("*")) {
      const escapedPattern = escapeRegExp(pattern);
      const regexPattern = escapedPattern.replace(/\\\*/g, ".*");
      const regex = new RegExp(
        `^${regexPattern}$|/${regexPattern}$|^${regexPattern}/|/${regexPattern}/`,
      );
      return regex.test(normalizedPath);
    }

    return (
      normalizedPath === pattern ||
      normalizedPath.startsWith(pattern + "/") ||
      normalizedPath.includes("/" + pattern + "/") ||
      normalizedPath.endsWith("/" + pattern)
    );
  });
}

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
  config: GraphConfig,
): Promise<string[]> {
  // Use unified shell executor
  const executor = createShellExecutor(config);
  const gitStatusOutput = await executor.executeCommand({
    command: "git status --porcelain",
    workdir: absoluteRepoDir,
    timeout: TIMEOUT_SEC,
    sandbox,
  });

  if (gitStatusOutput.exitCode !== 0) {
    logger.error(`Failed to get changed files status`, {
      gitStatusOutput,
    });
    return [];
  }

  return parseGitStatusOutput(gitStatusOutput.result);
}

export async function stashAndClearChanges(
  absoluteRepoDir: string,
  sandbox: Sandbox | null,
  config?: GraphConfig,
): Promise<ExecuteResponse | false> {
  // In local mode, we don't want to stash and clear changes
  if (config && isLocalMode(config)) {
    logger.info("Skipping stash and clear changes in local mode");
    return {
      exitCode: 0,
      result: "Skipped stash and clear in local mode",
    };
  }

  try {
    // Use unified shell executor
    const executor = createShellExecutor(config);
    const gitStashOutput = await executor.executeCommand({
      command: "git add -A && git stash && git reset --hard",
      workdir: absoluteRepoDir,
      timeout: TIMEOUT_SEC,
      sandbox: sandbox || undefined,
    });

    if (gitStashOutput.exitCode !== 0) {
      logger.error(`Failed to stash and clear changes`, {
        gitStashOutput,
      });
    }
    return gitStashOutput;
  } catch (e) {
    // Unified error handling
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

function constructCommitMessage(): string {
  const baseCommitMessage = "Apply patch";
  const skipCiString = "[skip ci]";
  const vercelSkipCi = process.env.SKIP_CI_UNTIL_LAST_COMMIT === "true";
  if (vercelSkipCi) {
    return `${baseCommitMessage} ${skipCiString}`;
  }
  return baseCommitMessage;
}

export async function checkoutBranchAndCommit(
  config: GraphConfig,
  targetRepository: TargetRepository,
  sandbox: Sandbox,
  options: {
    branchName?: string;
    githubInstallationToken: string;
    taskPlan: TaskPlan;
    githubIssueId: number;
  },
): Promise<{ branchName: string; updatedTaskPlan?: TaskPlan }> {
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
  const branchName = options.branchName || getBranchName(config);

  logger.info(`Committing changes to branch ${branchName}`);

  // Validate and filter files before committing
  const validFiles = await getValidFilesToCommit(
    absoluteRepoDir,
    sandbox,
    config,
  );

  if (validFiles.length === 0) {
    logger.info("No valid files to commit after filtering");
    return { branchName, updatedTaskPlan: options.taskPlan };
  }

  // Add only validated files instead of adding all files with "."
  await sandbox.git.add(absoluteRepoDir, validFiles);

  const botAppName = process.env.GITHUB_APP_NAME;
  if (!botAppName) {
    logger.error("GITHUB_APP_NAME environment variable is not set.");
    throw new Error("GITHUB_APP_NAME environment variable is not set.");
  }
  const userName = `${botAppName}[bot]`;
  const userEmail = `${botAppName}@users.noreply.github.com`;
  await sandbox.git.commit(
    absoluteRepoDir,
    constructCommitMessage(),
    userName,
    userEmail,
  );

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
    const errorFields =
      pushRes instanceof Error
        ? {
            message: pushRes.message,
            name: pushRes.name,
          }
        : pushRes;

    logger.error("Failed to push changes, attempting to pull and push again", {
      ...errorFields,
    });

    // attempt to git pull, then push again
    const pullRes = await withRetry(
      async () => {
        return await sandbox.git.pull(
          absoluteRepoDir,
          "git",
          options.githubInstallationToken,
        );
      },
      { retries: 1, delay: 0 },
    );

    if (pullRes instanceof Error) {
      const errorFields =
        pullRes instanceof Error
          ? {
              message: pullRes.message,
              name: pullRes.name,
            }
          : pullRes;
      logger.error("Failed to pull changes after a push failed.", {
        ...errorFields,
      });
    } else {
      logger.info("Successfully pulled changes. Pushing again.");
    }

    const pushRes2 = await withRetry(
      async () => {
        return await sandbox.git.push(
          absoluteRepoDir,
          "git",
          options.githubInstallationToken,
        );
      },
      { retries: 3, delay: 0 },
    );

    if (pushRes2 instanceof Error) {
      const gitStatus = await sandbox.git.status(absoluteRepoDir);
      const errorFields = {
        ...(pushRes2 instanceof Error
          ? {
              name: pushRes2.name,
              message: pushRes2.message,
              stack: pushRes2.stack,
              cause: pushRes2.cause,
            }
          : pushRes2),
      };
      logger.error("Failed to push changes", {
        ...errorFields,
        gitStatus: JSON.stringify(gitStatus, null, 2),
      });
      throw new Error("Failed to push changes");
    } else {
      logger.info("Pulling changes before pushing succeeded");
    }
  } else {
    logger.info("Successfully pushed changes");
  }

  // Check if the active task has a PR associated with it. If not, create a draft PR.
  let updatedTaskPlan: TaskPlan | undefined;
  const activeTask = getActiveTask(options.taskPlan);
  const prForTask = getPullRequestNumberFromActiveTask(options.taskPlan);
  if (!prForTask) {
    logger.info("First commit detected, creating a draft pull request.");
    const pullRequest = await createPullRequest({
      owner: targetRepository.owner,
      repo: targetRepository.repo,
      headBranch: branchName,
      title: `[WIP]: ${activeTask?.title ?? "Open SWE task"}`,
      body: `**WORK IN PROGRESS OPEN SWE PR**\n\nFixes: #${options.githubIssueId}`,
      githubInstallationToken: options.githubInstallationToken,
      draft: true,
      baseBranch: targetRepository.branch,
      nullOnError: true,
    });
    if (pullRequest) {
      updatedTaskPlan = addPullRequestNumberToActiveTask(
        options.taskPlan,
        pullRequest.number,
      );
      await addTaskPlanToIssue(
        {
          githubIssueId: options.githubIssueId,
          targetRepository,
        },
        config,
        updatedTaskPlan,
      );
      logger.info(`Draft pull request created: #${pullRequest.number}`);
    }
  }

  logger.info("Successfully checked out & committed changes.", {
    commitAuthor: userName,
  });

  return { branchName, updatedTaskPlan };
}

export async function pushEmptyCommit(
  targetRepository: TargetRepository,
  sandbox: Sandbox,
  config: GraphConfig,
  options: {
    githubInstallationToken: string;
  },
) {
  const botAppName = process.env.GITHUB_APP_NAME;
  if (!botAppName) {
    logger.error("GITHUB_APP_NAME environment variable is not set.");
    throw new Error("GITHUB_APP_NAME environment variable is not set.");
  }
  const userName = `${botAppName}[bot]`;
  const userEmail = `${botAppName}@users.noreply.github.com`;

  try {
    const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
    const executor = createShellExecutor(config);
    const setGitConfigRes = await executor.executeCommand({
      command: `git config user.name "${userName}" && git config user.email "${userEmail}"`,
      workdir: absoluteRepoDir,
      timeout: TIMEOUT_SEC,
    });
    if (setGitConfigRes.exitCode !== 0) {
      logger.error(`Failed to set git config`, {
        exitCode: setGitConfigRes.exitCode,
        result: setGitConfigRes.result,
      });
      return;
    }

    const emptyCommitRes = await executor.executeCommand({
      command: "git commit --allow-empty -m 'Empty commit to trigger CI'",
      workdir: absoluteRepoDir,
      timeout: TIMEOUT_SEC,
    });
    if (emptyCommitRes.exitCode !== 0) {
      logger.error(`Failed to push empty commit`, {
        exitCode: emptyCommitRes.exitCode,
        result: emptyCommitRes.result,
      });
      return;
    }

    await sandbox.git.push(
      absoluteRepoDir,
      "git",
      options.githubInstallationToken,
    );

    logger.info("Successfully pushed empty commit");
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error(`Failed to push empty commit`, {
      ...(errorFields && { errorFields }),
      ...(e instanceof Error && {
        name: e.name,
        message: e.message,
        stack: e.stack,
      }),
    });
  }
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

  if (!branchName && !targetRepository.baseCommit) {
    throw new Error(
      "Can not create new branch or checkout existing branch without branch name",
    );
  }

  const branchExists = branchName
    ? !!(await getBranch({
        owner: targetRepository.owner,
        repo: targetRepository.repo,
        branchName,
        githubInstallationToken,
      }))
    : false;

  if (branchExists) {
    logger.info("Branch already exists on remote. Cloning existing branch.", {
      branch: branchName,
    });
  }

  await sandbox.git.clone(
    cloneUrl,
    absoluteRepoDir,
    branchExists ? branchName : targetRepository.branch,
    branchExists ? undefined : targetRepository.baseCommit,
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
    throw new Error("Branch name is required");
  }

  if (branchExists) {
    return branchName;
  }

  try {
    logger.info("Creating branch", {
      branch: branchName,
    });

    await sandbox.git.createBranch(absoluteRepoDir, branchName);

    logger.info("Created branch", {
      branch: branchName,
    });
  } catch (error) {
    logger.error("Failed to create branch, checking out branch", {
      branch: branchName,
      error:
        error instanceof Error
          ? { name: error.name, message: error.message }
          : String(error),
    });
  }

  try {
    // push an empty commit so that the branch exists in the remote
    logger.info("Pushing empty commit to remote", {
      branch: branchName,
    });
    await sandbox.git.push(absoluteRepoDir, "git", githubInstallationToken);

    logger.info("Pushed empty commit to remote", {
      branch: branchName,
    });
  } catch (error) {
    logger.error("Failed to push an empty commit to branch", {
      branch: branchName,
      error:
        error instanceof Error
          ? { name: error.name, message: error.message }
          : String(error),
    });
  }

  return branchName;
}

export interface CheckoutFilesOptions {
  sandbox: Sandbox;
  repoDir: string;
  commitSha: string;
  filePaths: string[];
}

/**
 * Checkout specific files from a given commit
 */
export async function checkoutFilesFromCommit(
  options: CheckoutFilesOptions,
): Promise<void> {
  const { sandbox, repoDir, commitSha, filePaths } = options;

  if (filePaths.length === 0) {
    return;
  }

  logger.info(
    `Checking out ${filePaths.length} files from commit ${commitSha}`,
  );

  for (const filePath of filePaths) {
    try {
      const result = await sandbox.process.executeCommand(
        `git checkout --force ${commitSha} -- "${filePath}"`,
        repoDir,
        undefined,
        30,
      );

      if (result.exitCode !== 0) {
        logger.warn(
          `Failed to checkout file ${filePath} from commit ${commitSha}: ${result.result || "Unknown error"}`,
        );
      } else {
        logger.info(
          `Successfully checked out ${filePath} from commit ${commitSha}`,
        );
      }
    } catch (error) {
      logger.warn(`Error checking out file ${filePath}:`, { error });
    }
  }
}
