export interface SimplePullRequest {
  number: number;
  title: string;
  body: string | undefined;
  state: string;
  author: string | undefined;
  head: {
    ref: string;
    sha: string;
  };
  base: {
    ref: string;
    sha: string;
  };
}

export interface SimpleIssue {
  id: number;
  number: number;
  title: string;
  body: string | undefined;
  state: string;
  author: string | undefined;
}

export interface SimpleTriggerComment {
  id: number;
  body: string;
  author: string | undefined;
  path?: string;
  line?: number;
  diff_hunk?: string;
}

export interface SimplePullRequestComment {
  id: number;
  body: string | undefined;
  author: string | undefined;
}

export interface SimplePullRequestReviewComment {
  id: number;
  body: string | undefined;
  author: string | undefined;
  path: string;
  line: number | undefined;
  diff_hunk: string;
}

export interface SimplePullRequestReview {
  id: number;
  body: string | undefined;
  author: string | undefined;
  state: string;
  reviewComments: SimplePullRequestReviewComment[];
}

export interface PullRequestReviewTriggerData {
  pullRequest: SimplePullRequest;
  triggerComment: SimpleTriggerComment;
  prComments: SimplePullRequestComment[];
  reviews: SimplePullRequestReview[];
  linkedIssues: SimpleIssue[];
  repository: {
    owner: string;
    name: string;
  };
}
