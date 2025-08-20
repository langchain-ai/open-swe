import { describe, it, expect } from "@jest/globals";
import { Octokit } from "@octokit/core";
import { getPrContext } from "./utils.js";

/**
 * Integration test for getPrContext against a real GitHub PR.
 * Requires process.env.GITHUB_PAT_PR_REVIEW_TESTING to be set to a GitHub Personal Access Token
 * with issue and PR read permissions for a repo.
 */
describe("getPrContext integration - langchain-ai/open-swe-dev#725", () => {
  it("separates PR comments and review comments; finds expected messages", async () => {
    const token = process.env.GITHUB_PAT_PR_REVIEW_TESTING;
    if (!token) {
      return;
    }

    const octokit = new Octokit({ auth: token });

    const owner = "langchain-ai";
    const repo = "open-swe-dev";
    const prNumber = 725;

    const { prComments, reviews } = await getPrContext(octokit, {
      owner,
      repo,
      prNumber,
      linkedIssueNumbers: [],
    });

    expect(prComments).toHaveLength(2);

    // PR-level comment (issue comment)
    const hasNormalComment = prComments.some(
      (c) => (c.body ?? "").trim() === "this is a normal comment",
    );
    expect(hasNormalComment).toBe(true);

    expect(reviews).toHaveLength(1);

    // Review with CHANGES_REQUESTED and expected review body
    const changesRequestedReview = reviews.find(
      (r) =>
        (r.state ?? "").toUpperCase() === "CHANGES_REQUESTED" &&
        (r.body ?? "").trim() === "this is a review message",
    );
    expect(changesRequestedReview).toBeDefined();

    // Nested review comment
    const allReviewComments = reviews.flatMap((r) => r.reviewComments ?? []);
    expect(allReviewComments).toHaveLength(1);
    const hasReviewComment = allReviewComments.some(
      (rc) => (rc.body ?? "").trim() === "this is a review comment",
    );
    expect(hasReviewComment).toBe(true);

    // Ensure review comment is not duplicated in PR comments
    const prContainsReviewComment = prComments.some(
      (c) => (c.body ?? "").trim() === "this is a review comment",
    );
    expect(prContainsReviewComment).toBe(false);
  });
});
