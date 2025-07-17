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
): Promise<ExecuteResponse | false> {
  logger.info(`Checking out branch '${branchName}'...`);

  try {
    const getCurrentBranchOutput = await sandbox.process.executeCommand(
      "git branch --show-current",
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (getCurrentBranchOutput.exitCode !== 0) {
      logger.error(`Failed to get current branch`, {
        getCurrentBranchOutput,
      });
    } else {
      const currentBranch = getCurrentBranchOutput.result.trim();
      if (currentBranch === branchName) {
        logger.info(`Already on branch '${branchName}'. No checkout needed.`);
        return {
          result: `Already on branch ${branchName}`,
          exitCode: 0,
        };
      }
    }
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error(`Failed to get current branch`, {
      ...(errorFields && { errorFields }),
      ...(e instanceof Error && {
        name: e.name,
        message: e.message,
        stack: e.stack,
      }),
    });
    return false;
  }

  let checkoutCommand: string;
  try {
    logger.info(
      `Checking if branch 'refs/heads/${branchName}' exists using 'git rev-parse --verify --quiet'`,
    );
    // Check if branch exists using git rev-parse for robustness
    const checkBranchExistsOutput = await sandbox.process.executeCommand(
      `git rev-parse --verify --quiet "refs/heads/${branchName}"`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

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
      errorFields.result === ""
    ) {
      checkoutCommand = `git checkout -b "${branchName}"`;
    } else {
      logger.error(`Error checking if branch exists`, {
        ...(e instanceof Error && {
          name: e.name,
          message: e.message,
          stack: e.stack,
        }),
      });
      return false;
    }
  }

  try {
    const gitCheckoutOutput = await sandbox.process.executeCommand(
      checkoutCommand,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (gitCheckoutOutput.exitCode !== 0) {
      logger.error(`Failed to checkout branch`, {
        gitCheckoutOutput,
      });
      return false;
    }

    logger.info(`Checked out branch '${branchName}' successfully.`, {
      gitCheckoutOutput,
    });

    return gitCheckoutOutput;
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error(`Error checking out branch`, {
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

export async function configureGitUserInRepo(
  absoluteRepoDir: string,
  sandbox: Sandbox,
  args: {
    githubInstallationToken: string;
    owner: string;
    repo: string;
  },
): Promise<void> {
  const { githubInstallationToken, owner, repo } = args;
  let needsGitConfig = false;
  try {
    const nameCheck = await sandbox.process.executeCommand(
      "git config user.name",
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    const emailCheck = await sandbox.process.executeCommand(
      "git config user.email",
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (
      nameCheck.exitCode !== 0 ||
      nameCheck.result.trim() === "" ||
      emailCheck.exitCode !== 0 ||
      emailCheck.result.trim() === ""
    ) {
      needsGitConfig = true;
    }
  } catch (checkError) {
    logger.warn(`Could not check existing git config, will attempt to set it`, {
      ...(checkError instanceof Error && {
        name: checkError.name,
        message: checkError.message,
        stack: checkError.stack,
      }),
    });
    needsGitConfig = true;
  }

  // Configure git to use the token for authentication with GitHub by updating the remote URL
  logger.info(
    "Configuring git to use token for GitHub authentication via remote URL...",
  );
  try {
    // Set the remote URL with the token using the provided owner and repo
    const setRemoteOutput = await sandbox.process.executeCommand(
      `git remote set-url origin https://x-access-token:${githubInstallationToken}@github.com/${owner}/${repo}.git`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (setRemoteOutput.exitCode !== 0) {
      logger.error(`Failed to set remote URL with token`, {
        exitCode: setRemoteOutput.exitCode,
      });
    } else {
      logger.info("Git remote URL updated with token successfully.");
    }
  } catch (authError) {
    logger.error(`Error configuring git authentication for GitHub`, {
      ...(authError instanceof Error && {
        name: authError.name,
        message: authError.message,
        stack: authError.stack,
      }),
    });
  }

  if (needsGitConfig) {
    const botAppName = process.env.GITHUB_APP_NAME;
    if (!botAppName) {
      logger.error("GITHUB_APP_NAME environment variable is not set.");
      throw new Error("GITHUB_APP_NAME environment variable is not set.");
    }
    const userName = `${botAppName}[bot]`;
    const userEmail = `${botAppName}@users.noreply.github.com`;

    const configUserNameOutput = await sandbox.process.executeCommand(
      `git config user.name "${userName}"`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    if (configUserNameOutput.exitCode !== 0) {
      logger.error(`Failed to set git user.name`, {
        configUserNameOutput,
      });
    } else {
      logger.info(`Set git user.name to '${userName}' successfully.`);
    }

    const configUserEmailOutput = await sandbox.process.executeCommand(
      `git config user.email "${userEmail}"`,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    if (configUserEmailOutput.exitCode !== 0) {
      logger.error(`Failed to set git user.email`, {
        configUserEmailOutput,
      });
    } else {
      logger.info(`Set git user.email to '${userEmail}' successfully.`);
    }
  } else {
    logger.info(
      "Git user.name and user.email are already configured in this repository.",
    );
  }
}

export async function commitAll(
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

export async function commitAllAndPush(
  absoluteRepoDir: string,
  message: string,
  sandbox: Sandbox,
): Promise<ExecuteResponse | false> {
  try {
    const commitOutput = await commitAll(absoluteRepoDir, message, sandbox);
    logger.info(
      "Committed changes to git repository successfully. Now pushing...",
    );
    const pushCurrentBranchCmd =
      "git push -u origin $(git rev-parse --abbrev-ref HEAD)";

    if (!commitOutput || commitOutput.exitCode !== 0) {
      return false;
    }

    const gitPushOutput = await sandbox.process.executeCommand(
      pushCurrentBranchCmd,
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (gitPushOutput.exitCode !== 0) {
      logger.error(`Failed to push changes to git repository`, {
        gitPushOutput,
      });
      return false;
    }

    return gitPushOutput;
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error(`Failed to commit all and push changes to git repository`, {
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
  options?: {
    branchName?: string;
  },
): Promise<string> {
  logger.info("Checking out branch and committing changes...");
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
  const branchName = options?.branchName || getBranchName(config);

  await checkoutBranch(absoluteRepoDir, branchName, sandbox);

  logger.info(`Committing changes to branch ${branchName}`);
  await commitAllAndPush(absoluteRepoDir, "Apply patch", sandbox);
  logger.info("Successfully checked out & committed changes.");

  return branchName;
}

export async function pullLatestChanges(
  absoluteRepoDir: string,
  sandbox: Sandbox,
): Promise<ExecuteResponse | false> {
  try {
    const gitPullOutput = await sandbox.process.executeCommand(
      "git pull",
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );
    return gitPullOutput;
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

export async function cloneRepo(
  sandbox: Sandbox,
  targetRepository: TargetRepository,
  args: {
    githubInstallationToken: string;
    stateBranchName?: string;
  },
) {
  const absoluteRepoDir = getRepoAbsolutePath(targetRepository);
  let cloneResult: ExecuteResponse | null = null;

  try {
    const gitCloneCommand = ["git", "clone"];

    // Use x-access-token format for better GitHub authentication
    const repoUrlWithToken = `https://x-access-token:${args.githubInstallationToken}@github.com/${targetRepository.owner}/${targetRepository.repo}.git`;

    const branchName = args.stateBranchName || targetRepository.branch;
    if (branchName) {
      gitCloneCommand.push("-b", branchName, repoUrlWithToken);
    } else {
      gitCloneCommand.push(repoUrlWithToken);
    }

    logger.info("Cloning repository", {
      repoPath: `${targetRepository.owner}/${targetRepository.repo}`,
      branch: branchName,
      baseCommit: targetRepository.baseCommit,
      cloneCommand: ExecuteCommandError.cleanCommand(gitCloneCommand.join(" ")),
    });

    cloneResult = await sandbox.process.executeCommand(
      gitCloneCommand.join(" "),
    );

    if (!targetRepository.baseCommit) {
      if (cloneResult.exitCode !== 0) {
        if (!cloneResult.result.includes("not found in upstream origin")) {
          logger.error("Failed to clone repository", {
            targetRepository,
          });
          throw new ExecuteCommandError(gitCloneCommand.join(" "), cloneResult);
        } else {
          const cloneDefaultBranchCommand = ["git", "clone", repoUrlWithToken];
          logger.info(
            "Branch not found in upstream origin. Cloning default & checking out branch",
            {
              targetRepository,
              cloneDefaultBranchCommand: ExecuteCommandError.cleanCommand(
                cloneDefaultBranchCommand.join(" "),
              ),
            },
          );
          const cloneDefaultBranchResult = await sandbox.process.executeCommand(
            cloneDefaultBranchCommand.join(" "),
          );
          if (cloneDefaultBranchResult.exitCode !== 0) {
            logger.error("Failed to clone default branch", {
              targetRepository,
              cloneDefaultBranchCommand: ExecuteCommandError.cleanCommand(
                cloneDefaultBranchCommand.join(" "),
              ),
            });
            throw new ExecuteCommandError(
              cloneDefaultBranchCommand.join(" "),
              cloneDefaultBranchResult,
            );
          }

          cloneResult = cloneDefaultBranchResult;

          // Now checkout the branch. We're creating a new branch here since the above error indicated the branch doesn't exist.
          const checkoutBranchCommand = ["git", "checkout", "-b", branchName];
          const checkoutBranchResult = await sandbox.process.executeCommand(
            checkoutBranchCommand.join(" "),
            absoluteRepoDir,
            undefined,
            TIMEOUT_SEC,
          );
          if (checkoutBranchResult.exitCode !== 0) {
            logger.error("Failed to checkout branch", {
              targetRepository,
              checkoutBranchCommand: checkoutBranchCommand.join(" "),
            });
            throw new ExecuteCommandError(
              checkoutBranchCommand.join(" "),
              checkoutBranchResult,
            );
          }

          logger.info("Successfully checked out branch", {
            targetRepository,
            checkoutBranchCommand: checkoutBranchCommand.join(" "),
          });
        }
      }
      return cloneResult;
    }
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error("Clone repo failed\n", errorFields ?? e);
    throw e;
  }

  try {
    // If a baseCommit is specified, checkout that commit after cloning
    logger.info("Checking out base commit", {
      baseCommit: targetRepository.baseCommit,
      repoPath: `${targetRepository.owner}/${targetRepository.repo}`,
    });

    const checkoutCommitCommand = [
      "git",
      "checkout",
      targetRepository.baseCommit,
    ];
    const checkoutResult = await sandbox.process.executeCommand(
      checkoutCommitCommand.join(" "),
      absoluteRepoDir,
      undefined,
      TIMEOUT_SEC,
    );

    if (checkoutResult.exitCode !== 0) {
      logger.error("Failed to checkout base commit", {
        baseCommit: targetRepository.baseCommit,
        checkoutCommitCommand: checkoutCommitCommand.join(" "),
      });
      throw new ExecuteCommandError(
        checkoutCommitCommand.join(" "),
        checkoutResult,
      );
    }

    logger.info("Successfully checked out base commit", {
      baseCommit: targetRepository.baseCommit,
      checkoutCommitCommand: checkoutCommitCommand.join(" "),
    });
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error("Clone repo failed\n", errorFields ?? e);
    throw e;
  }

  return cloneResult;
}
