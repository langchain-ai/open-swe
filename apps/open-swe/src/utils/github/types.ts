import type { Endpoints } from "@octokit/types";

export type GitHubIssue =
  Endpoints["GET /repos/{owner}/{repo}/issues/{issue_number}"]["response"]["data"];

export type GitHubIssueComment =
  Endpoints["GET /repos/{owner}/{repo}/issues/{issue_number}/comments"]["response"]["data"][number];

export type GitHubPullRequest =
  Endpoints["POST /repos/{owner}/{repo}/pulls"]["response"]["data"];
