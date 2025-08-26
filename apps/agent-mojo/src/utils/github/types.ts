import type { RestEndpointMethodTypes } from "@octokit/rest";

export type GitHubIssue =
  RestEndpointMethodTypes["issues"]["get"]["response"]["data"];

export type GitHubIssueComment =
  RestEndpointMethodTypes["issues"]["listComments"]["response"]["data"][number];

export type GitHubPullRequest =
  RestEndpointMethodTypes["pulls"]["create"]["response"]["data"];

export type GitHubPullRequestUpdate =
  RestEndpointMethodTypes["pulls"]["update"]["response"]["data"];

export type GitHubPullRequestList =
  RestEndpointMethodTypes["pulls"]["list"]["response"]["data"];

export type GitHubBranch =
  RestEndpointMethodTypes["repos"]["getBranch"]["response"]["data"];
