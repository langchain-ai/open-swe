import type { RestEndpointMethodTypes } from "@octokit/rest";

export type GitHubIssue =
  RestEndpointMethodTypes["issues"]["get"]["response"]["data"];

export type GitHubIssueComment =
  RestEndpointMethodTypes["issues"]["listComments"]["response"]["data"][number];

export type GitHubPullRequest =
  RestEndpointMethodTypes["pulls"]["create"]["response"]["data"];
