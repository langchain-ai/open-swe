import { v4 as uuidv4 } from "uuid";
import { Octokit } from "@octokit/core";
import { GitHubPullRequestGet } from "../../utils/github/types.js";
import {
  SimpleIssue,
  SimplePullRequest,
  SimplePullRequestComment,
  SimplePullRequestReview,
} from "./types.js";
import { createLangGraphClient } from "../../utils/langgraph-client.js";
import {
  GITHUB_INSTALLATION_TOKEN_COOKIE,
  GITHUB_INSTALLATION_NAME,
  GITHUB_USER_ID_HEADER,
  GITHUB_USER_LOGIN_HEADER,
  GITHUB_INSTALLATION_ID,
  MANAGER_GRAPH_ID,
  OPEN_SWE_STREAM_MODE,
} from "@open-swe/shared/constants";
import { encryptSecret } from "@open-swe/shared/crypto";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { ManagerGraphUpdate } from "@open-swe/shared/open-swe/manager/types";
import { StreamMode } from "@langchain/langgraph-sdk";
import { extractContentWithoutDetailsFromIssueBody } from "../../utils/github/issue-messages.js";

export function createDevMetadataComment(runId: string, threadId: string) {
  return `<details>
  <summary>Dev Metadata</summary>
  ${JSON.stringify(
    {
      runId,
      threadId,
    },
    null,
    2,
  )}
</details>`;
}

export function mentionsOpenSWE(commentBody: string): boolean {
  return /@open-swe\b/.test(commentBody);
}

export function extractLinkedIssues(prBody: string): number[] {
  // Look for common patterns like "fixes #123", "closes #456", "resolves #789"
  const patterns = [
    /(?:fixes?|closes?|resolves?)\s+#(\d+)/gi,
    /(?:fix|close|resolve)\s+#(\d+)/gi,
  ];

  const issueNumbers: number[] = [];
  patterns.forEach((pattern) => {
    let match;
    while ((match = pattern.exec(prBody)) !== null) {
      issueNumbers.push(parseInt(match[1], 10));
    }
  });

  return [...new Set(issueNumbers)]; // Remove duplicates
}

/**
 * Fetches PR discussion context split into:
 * - prComments: top-level PR comments (issue comments on the PR)
 * - reviews: PR reviews including their own reviewComments
 */
export async function getPrContext(
  octokit: Octokit,
  inputs: {
    owner: string;
    repo: string;
    prNumber: number;
    linkedIssueNumbers: number[];
  },
): Promise<{
  prComments: SimplePullRequestComment[];
  reviews: SimplePullRequestReview[];
  linkedIssues: SimpleIssue[];
}> {
  const { owner, repo, prNumber, linkedIssueNumbers } = inputs;

  const [issueCommentsRes, reviewCommentsRes, reviewsRes] = await Promise.all([
    octokit.request(
      "GET /repos/{owner}/{repo}/issues/{issue_number}/comments",
      {
        owner,
        repo,
        issue_number: prNumber,
      },
    ),
    octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}/comments", {
      owner,
      repo,
      pull_number: prNumber,
    }),
    octokit.request("GET /repos/{owner}/{repo}/pulls/{pull_number}/reviews", {
      owner,
      repo,
      pull_number: prNumber,
    }),
  ]);

  const linkedIssuesRes = await Promise.all(
    linkedIssueNumbers.map((issueNumber) =>
      octokit.request("GET /repos/{owner}/{repo}/issues/{issue_number}", {
        owner,
        repo,
        issue_number: issueNumber,
      }),
    ),
  );

  const issueComments = issueCommentsRes.data;
  const allReviewComments = reviewCommentsRes.data;
  const reviews = reviewsRes.data;
  const linkedIssues = linkedIssuesRes.map((res) => res.data);

  // Group review comments by their parent review id
  const commentsByReviewId = new Map<number, any[]>();
  for (const c of allReviewComments) {
    const rid = c.pull_request_review_id as number | undefined;
    if (!rid) continue; // Only include comments that belong to a specific review
    const arr = commentsByReviewId.get(rid) ?? [];
    arr.push(c);
    commentsByReviewId.set(rid, arr);
  }

  return {
    prComments: issueComments.map((comment) => ({
      id: comment.id,
      body: comment.body,
      author: comment.user?.login,
    })),
    reviews: reviews.map((review) => ({
      id: review.id,
      body: review.body ?? undefined,
      author: review.user?.login,
      state: review.state,
      reviewComments: (commentsByReviewId.get(review.id) ?? []).map(
        (comment) => ({
          id: comment.id,
          body: comment.body,
          author: comment.user?.login,
          path: comment.path,
          line: comment.line,
          diff_hunk: comment.diff_hunk,
        }),
      ),
    })),
    linkedIssues: linkedIssues.map((issue) => ({
      id: issue.id,
      number: issue.number,
      title: issue.title,
      body: issue.body
        ? extractContentWithoutDetailsFromIssueBody(issue.body)
        : undefined,
      state: issue.state,
      author: issue.user?.login,
    })),
  };
}

export function convertPRPayloadToPullRequestObj(
  payloadPullRequest: GitHubPullRequestGet,
  prNumber: number,
): SimplePullRequest {
  return {
    number: prNumber,
    title: payloadPullRequest.title,
    body: payloadPullRequest.body ?? "",
    state: payloadPullRequest.state,
    author: payloadPullRequest.user?.login,
    head: {
      ref: payloadPullRequest.head.ref,
      sha: payloadPullRequest.head.sha,
    },
    base: {
      ref: payloadPullRequest.base.ref,
      sha: payloadPullRequest.base.sha,
    },
  };
}

export async function createRunFromWebhook(inputs: {
  installationId: number;
  installationToken: string;
  userId: number;
  userLogin: string;
  installationName: string;
  runInput: ManagerGraphUpdate;
  configurable?: Partial<GraphConfig["configurable"]>;
}): Promise<{
  runId: string;
  threadId: string;
}> {
  if (!process.env.SECRETS_ENCRYPTION_KEY) {
    throw new Error("SECRETS_ENCRYPTION_KEY environment variable is required");
  }
  const langGraphClient = createLangGraphClient({
    defaultHeaders: {
      [GITHUB_INSTALLATION_TOKEN_COOKIE]: encryptSecret(
        inputs.installationToken,
        process.env.SECRETS_ENCRYPTION_KEY,
      ),
      [GITHUB_INSTALLATION_NAME]: inputs.installationName,
      [GITHUB_USER_ID_HEADER]: inputs.userId.toString(),
      [GITHUB_USER_LOGIN_HEADER]: inputs.userLogin,
      [GITHUB_INSTALLATION_ID]: inputs.installationId.toString(),
    },
  });

  const threadId = uuidv4();

  const run = await langGraphClient.runs.create(threadId, MANAGER_GRAPH_ID, {
    input: inputs.runInput,
    config: {
      recursion_limit: 400,
      configurable: inputs.configurable,
    },
    ifNotExists: "create",
    streamResumable: true,
    streamMode: OPEN_SWE_STREAM_MODE as StreamMode[],
  });

  return {
    runId: run.run_id,
    threadId,
  };
}
