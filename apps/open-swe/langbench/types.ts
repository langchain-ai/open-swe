import { Sandbox } from "@daytonaio/sdk";

export interface PRData {
  url: string;
  htmlUrl: string;
  diffUrl: string;
  patchUrl: string;
  repoOwner: string;
  repoName: string;
  prNumber: number;
  mergeCommitSha: string;
  preMergeCommitSha: string;
  title: string;
  body: string;
  createdAt: string;
  mergedAt: string;
  testFiles: string[];
}

export interface TestResults {
  success: boolean;
  error: string | null;
  totalTests: number;
  passedTests: number;
  failedTests: number;
  testDetails: string[];
}

export interface PytestJsonTest {
  nodeid: string;
  outcome: "passed" | "failed" | "error" | "skipped";
}

export interface PytestJsonSummary {
  passed?: number;
  failed?: number;
  error?: number;
  skipped?: number;
}

export interface PytestJsonReport {
  tests?: PytestJsonTest[];
  summary?: PytestJsonSummary;
}

export interface PRProcessResult {
  prNumber: number;
  repoName: string;
  workspaceId?: string;
  success: boolean;
  evalsFound: boolean;
  evalsFiles: string[];
  testFiles: string[];
  testResults?: TestResults;
  error?: string;
  preMergeSha?: string;
}

export interface RunPytestOptions {
  sandbox: Sandbox;
  testFiles: string[];
  repoDir: string;
  timeoutSec?: number;
}
