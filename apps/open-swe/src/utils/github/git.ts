import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "../logger.js";
import { GraphConfig, TargetRepository } from "@open-swe/shared/open-swe/types";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getSandboxErrorFields } from "../sandbox-error-fields.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { ExecuteResponse } from "@daytonaio/sdk/src/types/ExecuteResponse.js";

class ExecuteCommandError extends Error {
  command: string;
  result: string;
  exitCode: number;
  constructor(command: string, error: ExecuteResponse) {
    super("Failed to execute command");
    this.name = "ExecuteCommandError";
    this.command = ExecuteCommandError.cleanCommand(command);
    this.result = error.result;
    this.exitCode = error.exitCode;
  }

  static cleanCommand(command: string): string {
    if (
      command.includes("x-access-token:") &&
      command.includes("@github.com/")
    ) {
      return command.replace(
        /(x-access-token:)([^@]+)(@github\.com\/)/,
        "$1ACCESS_TOKEN_REDACTED$3",
      );
    }
    return command;
  }
}

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

async function commitAll(
  absoluteRepoDir: string,
  message: string,
  sandbox: Sandbox,
): Promise<ExecuteResponse | false> {
  try {
    const gitAddOutput = await sandbox.process.executeCommand(
      `git add -A && git commit -m "${message}"`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (gitAddOutput.exitCode !== 0) {
      logger.error(`Failed to commit all changes to git repository`, {
        gitAddOutput,
      });
    }
    return gitAddOutput;
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error(`Failed to commit all changes to git repository`, {
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
): Promise<string> {
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
  const branchName = options.branchName || getBranchName(config);

  logger.info(`Committing changes to branch ${branchName}`);
  // Commit the changes. We can use the sandbox executeCommand API for this since it doesn't require a token.
  await sandbox.git.add(absoluteRepoDir, ["-A"]);

  const botAppName = process.env.GITHUB_APP_NAME;
  if (!botAppName) {
    logger.error("GITHUB_APP_NAME environment variable is not set.");
    throw new Error("GITHUB_APP_NAME environment variable is not set.");
  }
  const userName = `${botAppName}[bot]`;
  const userEmail = `${botAppName}@users.noreply.github.com`;
  await sandbox.git.commit(absoluteRepoDir, "Apply patch", userName, userEmail);

  // Push the changes using the git API so it handles authentication for us.
  await sandbox.git.push(
    absoluteRepoDir,
    "git",
    options.githubInstallationToken,
  );

  logger.info("Successfully checked out & committed changes.", {
    commitAuthor: userName,
  });

  return branchName;
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
    threadId?: string;
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
      threadId: args.threadId,
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
    threadId?: string;
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
    branchName,
    targetRepository.baseCommit,
    "git",
    githubInstallationToken,
  );
  logger.info("Successfully cloned repository", {
    repoPath: `${targetRepository.owner}/${targetRepository.repo}`,
    branch: branchName,
    baseCommit: targetRepository.baseCommit,
  });

  if (branchName) {
    return branchName;
  }

  // No branch name, checkout or create one
  if (!args.threadId) {
    throw new Error("Can not create new branch without thread ID");
  }
  const newBranchName = getBranchName(args.threadId);

  try {
    // No branch name, create one
    await sandbox.git.createBranch(absoluteRepoDir, newBranchName);
    logger.info("Created branch", {
      branch: newBranchName,
    });
    return newBranchName;
  } catch {
    // create failed, attempt to checkout branch
    logger.info("Failed to create branch, checking out branch", {
      branch: newBranchName,
    });
  }

  await sandbox.git.checkoutBranch(absoluteRepoDir, newBranchName);
  logger.info("Checked out branch", {
    branch: newBranchName,
  });
  return newBranchName;
}

/**
 * Handles the case where the specified branch doesn't exist by cloning default branch
 * and creating the new branch.
 */
export async function handleBranchNotFound(
  sandbox: Sandbox,
  cloneUrl: string,
  args: {
    branchName: string;
    targetRepository: TargetRepository;
    absoluteRepoDir: string;
  },
): Promise<ExecuteResponse> {
  const { branchName, targetRepository, absoluteRepoDir } = args;
  const cloneDefaultCommand = ["git", "clone", cloneUrl];

  logger.info(
    "Branch not found in upstream origin. Cloning default & checking out branch",
    {
      targetRepository,
      cloneDefaultCommand: ExecuteCommandError.cleanCommand(
        cloneDefaultCommand.join(" "),
      ),
    },
  );

  const cloneDefaultResult = await sandbox.process.executeCommand(
    cloneDefaultCommand.join(" "),
    undefined,
    undefined,
    TIMEOUT_SEC * 2,
  );

  if (cloneDefaultResult.exitCode !== 0) {
    logger.error("Failed to clone default branch", {
      targetRepository,
      cloneDefaultCommand: ExecuteCommandError.cleanCommand(
        cloneDefaultCommand.join(" "),
      ),
    });
    throw new ExecuteCommandError(
      cloneDefaultCommand.join(" "),
      cloneDefaultResult,
    );
  }

  // Create and checkout the new branch
  const checkoutCommand = ["git", "checkout", "-b", branchName];
  const checkoutResult = await sandbox.process.executeCommand(
    checkoutCommand.join(" "),
    absoluteRepoDir,
    undefined,
    TIMEOUT_SEC,
  );

  if (checkoutResult.exitCode !== 0) {
    logger.error("Failed to checkout branch", {
      targetRepository,
      checkoutCommand: checkoutCommand.join(" "),
    });
    throw new ExecuteCommandError(checkoutCommand.join(" "), checkoutResult);
  }

  logger.info("Successfully checked out branch", {
    targetRepository,
    checkoutCommand: checkoutCommand.join(" "),
  });

  return cloneDefaultResult;
}
