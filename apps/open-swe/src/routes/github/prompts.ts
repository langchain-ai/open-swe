import {
  createReplyToCommentToolFields,
  createReplyToReviewCommentToolFields,
} from "@open-swe/shared/open-swe/tools";
import {
  PullRequestReviewTriggerData,
  SimpleIssue,
  SimplePullRequest,
  SimplePullRequestComment,
  SimplePullRequestReview,
} from "./types.js";

const BASE_PR_TRIGGER_PROMPT = `<context-overview>
The context you're provided with to resolve the PR review is as follows:
- The pull request data (title, body, author, etc.). This may include context about the PR, why it was created, information about the changes, etc.
- The issue(s) that the PR will close when merged. Ensure you read these issue titles/descriptions so you have an idea as to the purpose of the PR.
- The comments left on the PR. These are important as they may include context about the PR, or feedback on the code which you should resolve.
  IMPORTANT: Keep in mind that some of these comments may already be resolved, so don't blindly make changes based on the comments alone. You mainly care about the actual PR review which was left on the PR.
- The reviews left on the PR. You're provided with all of the PR reviews left on this pull request. Each review may include a main review message, review comments, and a state (e.g. "approved", "changes requested"). You should focus on the latest review if there are multiple.
  - IMPORTANT: The comments on a review may reference specific lines of code. You should pay close attention to these comments and ensure you implement the changes in the simplest way possible.
</context-overview>

<pull-request-data>
Here is the data on the pull request you're resolving the review for:

{PR_DATA}

</pull-request-data>

<linked-issues>
Here are the issues which will be closed when this pull request is merged. Ensure you read over the issue titles/descriptions so you have an idea as to the purpose of the PR.

{LINKED_ISSUES}

</linked-issues>

<pull-request-comments>
Here are all of the comments (if any) which were left on the pull request.

{PR_COMMENTS}

</pull-request-comments>

<pull-request-reviews>
Here are all of the reviews which were left on the pull request.
If there are multiple, you should prioritize the reviews which are still "active" (e.g. changed requested, approved, or commented). However, still keep in mind the previous reviews for important context.

{PR_REVIEWS}

</pull-request-reviews>`;

// For PR review triggers
const PR_REVIEW_TRIGGER_PROMPT_PREFIX = `<instructions>
You're tasked with resolving all of the relevant comments/reviews which were left on this pull request.

For each comment, determine whether or not it needs a code change, and if so update the code to properly resolve the comment.
  IMPORTANT: Remember that some comments might already be resolved, so don't blindly make changes based on the comments alone. You mainly care about the actual PR review which was left on the PR.
For comments which do require code changes, you should implement the changes in the simplest way possible.
Ensure they're implemented to properly resolve the comment. Do not make any changes which are not directly related to resolving the comment.
Do not leave comments in your code about the review, or changes you're making.
After making a code change ensure you reply to the review comment which requested the change using the '${createReplyToReviewCommentToolFields().name}' tool. This message should be very short and to the point.

For comments which do not require code changes, you should either reply to the comment using the '${createReplyToReviewCommentToolFields().name}' tool, or ignore the comment if it's a no-op.

The changes you make will be put into a pull request which is set to be merged into the branch the review was left on. This will happen automatically for you.
</instructions>`;

const PR_REVIEW_TRIGGER_PROMPT_SUFFIX = `Given all of this context, please resolve the PR review comments in the simplest ways possible. You are only to make the code changes as requested in the review. A pull request will be automatically created for you with these changes that points to the original branch the review was left on.
You're already checked out on a new branch which is based on the original branch the review was left on. You should make all your changes on this branch.

IMPORTANT: The comments in the reviews should take precedence over the comments on the linked issue(s), or the body of the pull request/issue. Your main goal is to resolve all of the relevant review comments not yet addressed, in the simplest and most direct way possible.
`;

// For PR review comment triggers
const PR_REVIEW_COMMENT_TRIGGER_PROMPT_PREFIX = `<instructions>
You're tasked with resolving the pull request review comment which was left on this PR, and you (open-swe) were tagged in.

For the review comment, determine whether or not it needs a code change, and if so update the code to properly resolve the comment.
  IMPORTANT: Remember that some comments might already be resolved, so don't blindly make changes based on the comments alone. You mainly care about the actual PR review which was left on the PR.
If the review comment does require code changes, you should implement the changes in the simplest way possible.
Ensure they're implemented to properly resolve the comment. Do not make any changes which are not directly related to resolving the comment.
Do not leave comments in your code about the review, or changes you're making.
After making a code change ensure you reply to the review comment which requested the change using the '${createReplyToReviewCommentToolFields().name}' tool. This message should be very short and to the point.

If the review comment does not require code changes, you should either reply to the comment using the '${createReplyToReviewCommentToolFields().name}' tool, or ignore the comment if it's a no-op.

The changes you make will be put into a pull request which is set to be merged into the branch the review was left on. This will happen automatically for you.

REMINDER: You were tagged in a review comment. There may be many review comments which you're tagged in, so focus on the latest review comment. However you should still keep in mind all other comments as they may have useful context.
</instructions>`;

const PR_REVIEW_COMMENT_TRIGGER_PROMPT_SUFFIX = `Given all of this context, please resolve the latest PR review comment you were tagged in, in the simplest way possible. You are only to make the code changes as requested in the review comment. A pull request will be automatically created for you with these changes that points to the original branch the review was left on.
You're already checked out on a new branch which is based on the original branch the review was left on. You should make all your changes on this branch.

IMPORTANT: The review comment should take precedence over the comments on the linked issue(s), or the body of the pull request/issue. Your main goal is to resolve the review comment you were just tagged in, in the simplest and most direct way possible.
`;

// For PR comment triggers
const PR_COMMENT_TRIGGER_PROMPT_PREFIX = `<instructions>
The user has tagged you (open-swe) in a comment on this pull request. Your task is to resolve their comment in the simplest way possible.

Determine whether or not the comment requires a code change, and if so update the code to properly resolve the comment.
After making a code change ensure you reply to the comment which requested the change using the '${createReplyToCommentToolFields().name}' tool. This message should be very short and to the point.
For comments which do require code changes, you should implement the changes in the simplest way possible.
Ensure they're implemented to properly resolve the comment. Do not make any changes which are not directly related to resolving the comment.
Do not leave comments in your code about the review, or changes you're making.

For comments which do not require code changes, you should either reply to the comment using the '${createReplyToCommentToolFields().name}' tool, or ignore the comment if it's a no-op.

The changes you make will be put into a pull request which is set to be merged into the branch the comment was left on. This will happen automatically for you.
</instructions>`;

const PR_COMMENT_TRIGGER_PROMPT_SUFFIX = `Given all of this context, please resolve the comment you were tagged in, in the simplest ways possible. You are only to make the code changes as requested in the comment. A pull request will be automatically created for you with these changes that points to the original branch the comment was left on.
You're already checked out on a new branch which is based on the original branch the comment was left on. You should make all your changes on this branch.

IMPORTANT: The comment should take precedence over the comments on the linked issue(s), or the body of the pull request/issue. Your main goal is to resolve the comment you were tagged in, in the simplest and most direct way possible.
`;

function formatLinkedIssuesPrompt(issues: SimpleIssue[]): string {
  if (!issues.length) {
    return "No linked issues";
  }

  return issues
    .map(
      (issue) => `<issue number="${issue.number}">
      <state>${issue.state}</state>
      <title>${issue.title}</title>
      <body>${issue.body ?? "No body"}</body>
</issue>
`,
    )
    .join("\n");
}

function formatPRCommentsPrompt(comments: SimplePullRequestComment[]): string {
  if (!comments.length) {
    return "No comments";
  }

  return comments
    .map(
      (comment) => `<comment id="${comment.id}">
      <author>${comment.author}</author>
      <body>${comment.body}</body>
</comment>
`,
    )
    .join("\n");
}

function formatPRReviewsPrompt(reviews: SimplePullRequestReview[]): string {
  if (!reviews.length) {
    return "No reviews";
  }

  return reviews
    .map(
      (review) => `<review id="${review.id}">
  <author>${review.author}</author>
  <body>${review.body ?? "No review body"}</body>
  <state>${review.state}</state>

    ${review.reviewComments.map(
      (comment) => `<review-comment id="${comment.id}">
      <body>${comment.body ?? "No review comment body"}</body>
      <path>${comment.path}</path>
      <line>${comment.line}</line>
      <diff-hunk>
        ${comment.diff_hunk}
      </diff-hunk>
    </review-comment>`,
    )}

</review>
`,
    )
    .join("\n");
}

function formatPRDataPrompt(prData: SimplePullRequest): string {
  return `<title>\n${prData.title}</title>
<body>${prData.body}</body>
<author>${prData.author}</author>
<state>${prData.state}</state>

<head-ref>${prData.head.ref}</head-ref>`;
}

export function createPromptFromPRReviewTrigger(
  data: PullRequestReviewTriggerData,
): string {
  const formattedPrompt = BASE_PR_TRIGGER_PROMPT.replace(
    "{PR_DATA}",
    formatPRDataPrompt(data.pullRequest),
  )
    .replace("{LINKED_ISSUES}", formatLinkedIssuesPrompt(data.linkedIssues))
    .replace("{PR_COMMENTS}", formatPRCommentsPrompt(data.prComments))
    .replace("{PR_REVIEWS}", formatPRReviewsPrompt(data.reviews));

  return (
    PR_REVIEW_TRIGGER_PROMPT_PREFIX +
    "\n\n" +
    formattedPrompt +
    "\n\n" +
    PR_REVIEW_TRIGGER_PROMPT_SUFFIX
  );
}

export function createPromptFromPRReviewCommentTrigger(
  data: PullRequestReviewTriggerData,
): string {
  const formattedPrompt = BASE_PR_TRIGGER_PROMPT.replace(
    "{PR_DATA}",
    formatPRDataPrompt(data.pullRequest),
  )
    .replace("{LINKED_ISSUES}", formatLinkedIssuesPrompt(data.linkedIssues))
    .replace("{PR_COMMENTS}", formatPRCommentsPrompt(data.prComments))
    .replace("{PR_REVIEWS}", formatPRReviewsPrompt(data.reviews));

  return (
    PR_REVIEW_COMMENT_TRIGGER_PROMPT_PREFIX +
    "\n\n" +
    formattedPrompt +
    "\n\n" +
    PR_REVIEW_COMMENT_TRIGGER_PROMPT_SUFFIX
  );
}

export function createPromptFromPRCommentTrigger(
  data: PullRequestReviewTriggerData,
): string {
  const formattedPrompt = BASE_PR_TRIGGER_PROMPT.replace(
    "{PR_DATA}",
    formatPRDataPrompt(data.pullRequest),
  )
    .replace("{LINKED_ISSUES}", formatLinkedIssuesPrompt(data.linkedIssues))
    .replace("{PR_COMMENTS}", formatPRCommentsPrompt(data.prComments))
    .replace("{PR_REVIEWS}", formatPRReviewsPrompt(data.reviews));

  return (
    PR_COMMENT_TRIGGER_PROMPT_PREFIX +
    "\n\n" +
    formattedPrompt +
    "\n\n" +
    PR_COMMENT_TRIGGER_PROMPT_SUFFIX
  );
}
