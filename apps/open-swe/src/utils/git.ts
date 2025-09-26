import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import { markWorkspaceGitConfigured, isWorkspaceGitConfigured, logWorkspaceCommit } from "./workspace.js";

const execFileAsync = promisify(execFile);

const DEFAULT_COMMIT_MESSAGE = "OpenSWE auto-commit";
const DEFAULT_COMMIT_AUTHOR_NAME = "Open SWE";
const DEFAULT_COMMIT_AUTHOR_EMAIL = "opensource@langchain.dev";

const commitCounters = new Map<string, number>();

function getGitEnv() {
  const authorName = process.env.GIT_AUTHOR_NAME?.trim() || DEFAULT_COMMIT_AUTHOR_NAME;
  const authorEmail = process.env.GIT_AUTHOR_EMAIL?.trim() || DEFAULT_COMMIT_AUTHOR_EMAIL;
  const committerName = process.env.GIT_COMMITTER_NAME?.trim() || authorName;
  const committerEmail = process.env.GIT_COMMITTER_EMAIL?.trim() || authorEmail;

  return {
    ...process.env,
    GIT_AUTHOR_NAME: authorName,
    GIT_AUTHOR_EMAIL: authorEmail,
    GIT_COMMITTER_NAME: committerName,
    GIT_COMMITTER_EMAIL: committerEmail,
  };
}

function buildCommitMessage(repoPath: string): string {
  const count = (commitCounters.get(repoPath) ?? 0) + 1;
  commitCounters.set(repoPath, count);
  const suffix = process.env.SKIP_CI_UNTIL_LAST_COMMIT === "false" ? "" : " [skip ci]";
  return `${DEFAULT_COMMIT_MESSAGE} #${count}${suffix}`;
}

async function execGitCommand(args: string[], cwd: string) {
  try {
    const result = await execFileAsync("git", args, { cwd, env: getGitEnv() });
    return { stdout: result.stdout.toString(), stderr: result.stderr.toString() };
  } catch (error) {
    if (error && typeof error === "object" && "stdout" in error) {
      return {
        stdout: String((error as { stdout?: string }).stdout ?? ""),
        stderr: String((error as { stderr?: string }).stderr ?? ""),
      };
    }
    throw error;
  }
}

export async function configureGitWorkspace(repoPath: string): Promise<void> {
  const normalized = path.resolve(repoPath);
  if (isWorkspaceGitConfigured(normalized)) {
    return;
  }

  await execGitCommand(["config", "--global", "--add", "safe.directory", normalized], normalized);
  const env = getGitEnv();
  if (env.GIT_COMMITTER_NAME) {
    await execGitCommand(
      ["config", "--global", "user.name", env.GIT_COMMITTER_NAME],
      normalized,
    );
  }
  if (env.GIT_COMMITTER_EMAIL) {
    await execGitCommand(
      ["config", "--global", "user.email", env.GIT_COMMITTER_EMAIL],
      normalized,
    );
  }

  markWorkspaceGitConfigured(normalized);
}

export async function stageAndCommitWorkspaceChanges(
  repoPath: string,
): Promise<string | null> {
  const normalized = path.resolve(repoPath);
  await configureGitWorkspace(normalized);

  await execGitCommand(["add", "-A"], normalized);
  const diff = await execGitCommand(["diff", "--cached", "--name-only"], normalized);
  if (!diff.stdout.trim()) {
    return null;
  }

  const message = buildCommitMessage(normalized);
  const commitResult = await execGitCommand(["commit", "-m", message], normalized);
  logWorkspaceCommit(normalized, message, commitResult);
  return message;
}
