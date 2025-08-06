import { Octokit } from "@octokit/rest";
import { createLogger, LogLevel } from "../logger.js";
import {
  GitHubBranch,
  GitHubIssue,
  GitHubIssueComment,
  GitHubPullRequest,
  GitHubPullRequestList,
  GitHubPullRequestUpdate,
} from "./types.js";
import { getOpenSWELabel } from "./label.js";
import { getInstallationToken } from "@open-swe/shared/github/auth";
import { getConfig } from "@langchain/langgraph";
import { GITHUB_INSTALLATION_ID } from "@open-swe/shared/constants";
import { updateConfig } from "../update-config.js";
import { encryptSecret } from "@open-swe/shared/crypto";

const logger = createLogger(LogLevel.INFO, "GitHub-API");

async function getInstallationTokenAndUpdateConfig() {
  try {
    logger.info("Fetching a new GitHub installation token.");
    const config = getConfig();
    const encryptionSecret = process.env.SECRETS_ENCRYPTION_KEY;
    if (!encryptionSecret) {
      throw new Error("Secrets encryption key not found");
    }

    const installationId = config.configurable?.[GITHUB_INSTALLATION_ID];
    const appId = process.env.GITHUB_APP_ID;
    const privateKey = process.env.GITHUB_APP_PRIVATE_KEY;
    if (!installationId || !appId || !privateKey) {
      throw new Error(
        "GitHub installation ID, app ID, or private key not found",
      );
    }

    const token = await getInstallationToken(installationId, appId, privateKey);
    const encryptedToken = encryptSecret(token, encryptionSecret);
    updateConfig(GITHUB_INSTALLATION_ID, encryptedToken);
    logger.info("Successfully fetched a new GitHub installation token.");
    return token;
  } catch (e) {
    logger.error("Failed to get installation token and update config", {
      error: e,
    });
    return null;
  }
}

/**
 * Generic utility for handling GitHub API calls with automatic retry on 401 errors
 */
async function withGitHubRetry<T>(
  operation: (token: string) => Promise<T>,
  initialToken: string,
  errorMessage: string,
  additionalLogFields?: Record<string, any>,
  numRetries = 1,
): Promise<T | null> {
  try {
    return await operation(initialToken);
  } catch (error) {
    const errorFields =
      error instanceof Error
        ? {
            name: error.name,
            message: error.message,
            stack: error.stack,
          }
        : {};

    // Retry with a max retries of 2
    if (errorFields && errorFields.message?.includes("401") && numRetries < 2) {
      const token = await getInstallationTokenAndUpdateConfig();
      if (!token) {
        return null;
      }
      return withGitHubRetry(
        operation,
        token,
        errorMessage,
        additionalLogFields,
        numRetries + 1,
      );
    }

    logger.error(errorMessage, {
      numRetries,
      ...additionalLogFields,
      ...(errorFields ?? { error }),
    });
    return null;
  }
}

async function getExistingPullRequest(
  owner: string,
  repo: string,
  branchName: string,
  githubToken: string,
  numRetries = 1,
): Promise<GitHubPullRequestList[number] | null> {
  return withGitHubRetry(
    async (token: string) => {
      const octokit = new Octokit({
        auth: token,
      });

      const { data: pullRequests } = await octokit.pulls.list({
        owner,
        repo,
        head: branchName,
      });

      return pullRequests?.[0] || null;
    },
    githubToken,
    "Failed to get existing pull request",
    { branch: branchName, owner, repo },
    numRetries,
  );
}

export async function createPullRequest({
  owner,
  repo,
  headBranch,
  title,
  body = "",
  githubInstallationToken,
  baseBranch,
  draft = false,
  nullOnError = false,
}: {
  owner: string;
  repo: string;
  headBranch: string;
  title: string;
  body?: string;
  githubInstallationToken: string;
  baseBranch?: string;
  draft?: boolean;
  nullOnError?: boolean;
}): Promise<GitHubPullRequest | GitHubPullRequestList[number] | null> {
  const octokit = new Octokit({
    auth: githubInstallationToken,
  });

  let repoBaseBranch = baseBranch;
  if (!repoBaseBranch) {
    try {
      logger.info("Fetching default branch from repo", {
        owner,
        repo,
      });
      const { data: repository } = await octokit.repos.get({
        owner,
        repo,
      });

      repoBaseBranch = repository.default_branch;
      if (!repoBaseBranch) {
        throw new Error("No base branch returned after fetching repo");
      }
      logger.info("Fetched default branch from repo", {
        owner,
        repo,
        baseBranch: repoBaseBranch,
      });
    } catch (e) {
      logger.error("Failed to fetch base branch from repo", {
        owner,
        repo,
        ...(e instanceof Error && {
          name: e.name,
          message: e.message,
          stack: e.stack,
        }),
      });
      return null;
    }
  }

  let pullRequest: GitHubPullRequest | null = null;
  try {
    logger.info(
      `Creating pull request against default branch: ${repoBaseBranch}`,
      { nullOnError },
    );

    // Step 2: Create the pull request
    const { data: pullRequestData } = await octokit.pulls.create({
      draft,
      owner,
      repo,
      title,
      body,
      head: headBranch,
      base: repoBaseBranch,
    });

    pullRequest = pullRequestData;
    logger.info(`🐙 Pull request created: ${pullRequest.html_url}`);
  } catch (error) {
    if (nullOnError) {
      return null;
    }

    if (error instanceof Error && error.message.includes("already exists")) {
      logger.info(
        "Pull request already exists. Getting existing pull request...",
        {
          nullOnError,
        },
      );
      return getExistingPullRequest(
        owner,
        repo,
        headBranch,
        githubInstallationToken,
      );
    }

    logger.error(`Failed to create pull request`, {
      error,
    });
    return null;
  }

  try {
    logger.info("Adding 'open-swe' label to pull request", {
      pullRequestNumber: pullRequest.number,
    });
    await octokit.issues.addLabels({
      owner,
      repo,
      issue_number: pullRequest.number,
      labels: [getOpenSWELabel()],
    });
    logger.info("Added 'open-swe' label to pull request", {
      pullRequestNumber: pullRequest.number,
    });
  } catch (labelError) {
    logger.warn("Failed to add 'open-swe' label to pull request", {
      pullRequestNumber: pullRequest.number,
      labelError,
    });
  }

  return pullRequest;
}

export async function markPullRequestReadyForReview({
  owner,
  repo,
  pullNumber,
  title,
  body,
  githubInstallationToken,
}: {
  owner: string;
  repo: string;
  pullNumber: number;
  title: string;
  body: string;
  githubInstallationToken: string;
}): Promise<GitHubPullRequestUpdate | null> {
  return withGitHubRetry(
    async (token: string) => {
      const octokit = new Octokit({
        auth: token,
      });

      // Fetch the PR, as the markReadyForReview mutation requires the PR's node ID, not the pull number
      const { data: pr } = await octokit.pulls.get({
        owner,
        repo,
        pull_number: pullNumber,
      });

      await octokit.graphql(
        `
        mutation MarkPullRequestReadyForReview($pullRequestId: ID!) {
          markPullRequestReadyForReview(input: {
            pullRequestId: $pullRequestId
          }) {
            clientMutationId
            pullRequest {
              id
              number
              isDraft
            }
          }
        }
      `,
        {
          pullRequestId: pr.node_id,
        },
      );

      const { data: updatedPR } = await octokit.pulls.update({
        owner,
        repo,
        pull_number: pullNumber,
        title,
        body,
      });

      logger.info(`Pull request #${pullNumber} marked as ready for review.`);
      return updatedPR;
    },
    githubInstallationToken,
    "Failed to mark pull request as ready for review",
    { pullNumber, owner, repo },
    1,
  );
}

export async function getIssue({
  owner,
  repo,
  issueNumber,
  githubInstallationToken,
  numRetries = 1,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  githubInstallationToken: string;
  numRetries?: number;
}): Promise<GitHubIssue | null> {
  return withGitHubRetry(
    async (token: string) => {
      const octokit = new Octokit({
        auth: token,
      });

      const { data: issue } = await octokit.issues.get({
        owner,
        repo,
        issue_number: issueNumber,
      });

      return issue;
    },
    githubInstallationToken,
    "Failed to get issue",
    undefined,
    numRetries,
  );
}

export async function getIssueComments({
  owner,
  repo,
  issueNumber,
  githubInstallationToken,
  filterBotComments,
  numRetries = 1,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  githubInstallationToken: string;
  filterBotComments: boolean;
  numRetries?: number;
}): Promise<GitHubIssueComment[] | null> {
  return withGitHubRetry(
    async (token: string) => {
      const octokit = new Octokit({
        auth: token,
      });

      const { data: comments } = await octokit.issues.listComments({
        owner,
        repo,
        issue_number: issueNumber,
      });

      if (!filterBotComments) {
        return comments;
      }

      return comments.filter(
        (comment) =>
          comment.user?.type !== "Bot" &&
          !comment.user?.login?.includes("[bot]"),
      );
    },
    githubInstallationToken,
    "Failed to get issue comments",
    undefined,
    numRetries,
  );
}

export async function createIssue({
  owner,
  repo,
  title,
  body,
  githubAccessToken,
}: {
  owner: string;
  repo: string;
  title: string;
  body: string;
  githubAccessToken: string;
}): Promise<GitHubIssue | null> {
  const octokit = new Octokit({
    auth: githubAccessToken,
  });

  try {
    const { data: issue } = await octokit.issues.create({
      owner,
      repo,
      title,
      body,
    });

    return issue;
  } catch (error) {
    const errorFields =
      error instanceof Error
        ? {
            name: error.name,
            message: error.message,
            stack: error.stack,
          }
        : { error };
    logger.error(`Failed to create issue`, errorFields);
    return null;
  }
}

export async function updateIssue({
  owner,
  repo,
  issueNumber,
  githubInstallationToken,
  body,
  title,
  numRetries = 1,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  githubInstallationToken: string;
  body?: string;
  title?: string;
  numRetries?: number;
}) {
  if (!body && !title) {
    throw new Error("Must provide either body or title to update issue");
  }

  return withGitHubRetry(
    async (token: string) => {
      const octokit = new Octokit({
        auth: token,
      });

      const { data: issue } = await octokit.issues.update({
        owner,
        repo,
        issue_number: issueNumber,
        ...(body && { body }),
        ...(title && { title }),
      });

      return issue;
    },
    githubInstallationToken,
    "Failed to update issue",
    undefined,
    numRetries,
  );
}

export async function createIssueComment({
  owner,
  repo,
  issueNumber,
  body,
  githubToken,
  numRetries = 1,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  body: string;
  /**
   * Can be either the installation token if creating a bot comment,
   * or an access token if creating a user comment.
   */
  githubToken: string;
  numRetries?: number;
}): Promise<GitHubIssueComment | null> {
  return withGitHubRetry(
    async (token: string) => {
      const octokit = new Octokit({
        auth: token,
      });

      const { data: comment } = await octokit.issues.createComment({
        owner,
        repo,
        issue_number: issueNumber,
        body,
      });

      return comment;
    },
    githubToken,
    "Failed to create issue comment",
    undefined,
    numRetries,
  );
}

export async function updateIssueComment({
  owner,
  repo,
  commentId,
  body,
  githubInstallationToken,
  numRetries = 1,
}: {
  owner: string;
  repo: string;
  commentId: number;
  body: string;
  githubInstallationToken: string;
  numRetries?: number;
}): Promise<GitHubIssueComment | null> {
  return withGitHubRetry(
    async (token: string) => {
      const octokit = new Octokit({
        auth: token,
      });

      const { data: comment } = await octokit.issues.updateComment({
        owner,
        repo,
        comment_id: commentId,
        body,
      });

      return comment;
    },
    githubInstallationToken,
    "Failed to update issue comment",
    undefined,
    numRetries,
  );
}

export async function getBranch({
  owner,
  repo,
  branchName,
  githubInstallationToken,
}: {
  owner: string;
  repo: string;
  branchName: string;
  githubInstallationToken: string;
}): Promise<GitHubBranch | null> {
  return withGitHubRetry(
    async (token: string) => {
      const octokit = new Octokit({
        auth: token,
      });

      const { data: branch } = await octokit.repos.getBranch({
        owner,
        repo,
        branch: branchName,
      });

      return branch;
    },
    githubInstallationToken,
    "Failed to get branch",
    undefined,
    1,
  );
}
