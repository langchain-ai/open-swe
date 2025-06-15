import { Octokit } from "@octokit/rest";
import { createLogger, LogLevel } from "../logger.js";
import { GitHubIssue, GitHubIssueComment } from "./types.js";

const logger = createLogger(LogLevel.INFO, "GitHub-API");

async function getExistingPullRequest(
  owner: string,
  repo: string,
  branchName: string,
  githubToken: string,
) {
  try {
    const octokit = new Octokit({
      auth: githubToken,
    });

    const { data: pullRequests } = await octokit.pulls.list({
      owner,
      repo,
      head: branchName,
    });

    if (pullRequests?.[0]) {
      return pullRequests[0];
    }
  } catch (e) {
    logger.error(`Failed to get existing pull request`, {
      branch: branchName,
      owner,
      repo,
      ...(e instanceof Error && {
        name: e.name,
        message: e.message,
        stack: e.stack,
      }),
    });
  }

  return null;
}

export async function createPullRequest({
  owner,
  repo,
  headBranch,
  title,
  body = "",
  githubInstallationToken,
}: {
  owner: string;
  repo: string;
  headBranch: string;
  title: string;
  body?: string;
  githubInstallationToken: string;
}) {
  const octokit = new Octokit({
    auth: githubInstallationToken,
  });

  try {
    // Step 1: Get repository information to find the default branch
    const { data: repository } = await octokit.repos.get({
      owner,
      repo,
    });

    const defaultBranch = repository.default_branch;
    logger.info(
      `Creating pull request against default branch: ${defaultBranch}`,
    );

    // Step 2: Create the pull request
    const { data: pullRequest } = await octokit.pulls.create({
      owner,
      repo,
      title,
      body,
      head: headBranch,
      base: defaultBranch,
    });

    logger.info(`🐙 Pull request created: ${pullRequest.html_url}`);

    // Step 3: Add the 'open-swe' label to the pull request
    try {
      await octokit.issues.addLabels({
        owner,
        repo,
        issue_number: pullRequest.number,
        labels: ["open-swe"],
      });
      logger.info(
        `Added 'open-swe' label to pull request #${pullRequest.number}`,
      );
    } catch (labelError) {
      logger.warn(
        `Failed to add 'open-swe' label to pull request #${pullRequest.number}`,
        {
          labelError,
        },
      );
    }

    return pullRequest;
  } catch (error) {
    if (error instanceof Error && error.message.includes("already exists")) {
      logger.info(
        "Pull request already exists. Getting existing pull request...",
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
}

export async function getIssue({
  owner,
  repo,
  issueNumber,
  githubInstallationToken,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  githubInstallationToken: string;
}): Promise<GitHubIssue | null> {
  const octokit = new Octokit({
    auth: githubInstallationToken,
  });

  try {
    const { data: issue } = await octokit.issues.get({
      owner,
      repo,
      issue_number: issueNumber,
    });

    return issue;
  } catch (error) {
    logger.error(`Failed to get issue`, {
      error,
    });
    return null;
  }
}

export async function getIssueComments({
  owner,
  repo,
  issueNumber,
  githubInstallationToken,
  filterBotComments = true,
}: {
  owner: string;
  repo: string;
  issueNumber: number;
  githubInstallationToken: string;
  filterBotComments?: boolean;
}): Promise<GitHubIssueComment[] | null> {
  const octokit = new Octokit({
    auth: githubInstallationToken,
  });

  try {
    const { data: comments } = await octokit.issues.listComments({
      owner,
      repo,
      issue_number: issueNumber,
    });

    if (!filterBotComments) {
      return comments;
    }

    return comments.filter((comment) => {
      return (
        comment.user?.type !== "Bot" || !comment.user?.name?.includes("[bot]")
      );
    });
  } catch (error) {
    logger.error(`Failed to get issue comments`, {
      error,
    });
    return null;
  }
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
    logger.error(`Failed to create issue`, {
      error,
    });
    return null;
  }
}

export async function createIssueComment({
  owner,
  repo,
  issueNumber,
  body,
  githubToken,
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
}): Promise<GitHubIssueComment | null> {
  const octokit = new Octokit({
    auth: githubToken,
  });

  try {
    const { data: comment } = await octokit.issues.createComment({
      owner,
      repo,
      issue_number: issueNumber,
      body,
    });

    return comment;
  } catch (error) {
    logger.error(`Failed to create issue comment`, {
      error,
    });
    return null;
  }
}
