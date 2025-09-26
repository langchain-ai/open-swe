import fs from "node:fs";
import path from "node:path";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "WorkspaceUtils");

function normalizeRoot(root: string): string {
  const resolved = path.resolve(root);
  return resolved.endsWith(path.sep) ? resolved : `${resolved}${path.sep}`;
}

export function resolveWorkspacePath(workspaceAbsPath: string | undefined): string {
  const workspacesRoot = process.env.WORKSPACES_ROOT;
  if (!workspacesRoot) {
    throw new Error("WORKSPACES_ROOT environment variable is not set.");
  }

  if (!workspaceAbsPath || workspaceAbsPath.trim().length === 0) {
    throw new Error("workspaceAbsPath is required.");
  }

  let resolvedRoot: string;
  let resolvedWorkspace: string;
  try {
    resolvedRoot = fs.realpathSync(workspacesRoot);
  } catch (error) {
    throw new Error(
      `Unable to resolve workspaces root: ${String(
        error instanceof Error ? error.message : error,
      )}.`,
    );
  }

  try {
    resolvedWorkspace = fs.realpathSync(workspaceAbsPath);
  } catch (error) {
    throw new Error(
      `Unable to resolve workspace path: ${String(
        error instanceof Error ? error.message : error,
      )}.`,
    );
  }

  const normalizedRoot = normalizeRoot(resolvedRoot);
  if (
    resolvedWorkspace !== resolvedRoot &&
    !resolvedWorkspace.startsWith(normalizedRoot)
  ) {
    throw new Error(
      `Resolved workspace path "${resolvedWorkspace}" is outside of the configured root "${resolvedRoot}".`,
    );
  }

  return resolvedWorkspace;
}

export function getWorkspacePathFromConfig(config?: GraphConfig): string | undefined {
  const configurable = config?.configurable as Record<string, unknown> | undefined;
  const configuredPath = configurable?.workspacePath;
  if (typeof configuredPath === "string" && configuredPath.trim().length > 0) {
    return configuredPath;
  }

  const envPath = process.env.OPEN_SWE_WORKSPACE_PATH;
  if (envPath && envPath.trim().length > 0) {
    return path.resolve(envPath);
  }

  return undefined;
}

export function resolvePathInsideWorkspace(
  workspacePath: string,
  filePath: string,
  workDir?: string,
): string {
  const resolvedWorkspace = path.resolve(workspacePath);
  const baseDir = workDir
    ? path.isAbsolute(workDir)
      ? workDir
      : path.resolve(resolvedWorkspace, workDir)
    : resolvedWorkspace;
  const candidate = path.isAbsolute(filePath)
    ? path.resolve(filePath)
    : path.resolve(baseDir, filePath);

  const normalizedWorkspace = normalizeRoot(resolvedWorkspace);
  if (candidate !== resolvedWorkspace && !candidate.startsWith(normalizedWorkspace)) {
    throw new Error(
      `Resolved path "${candidate}" is outside of the workspace root "${resolvedWorkspace}".`,
    );
  }

  return candidate;
}

const configuredGitWorkspaces = new Set<string>();

export function markWorkspaceGitConfigured(workspacePath: string): void {
  configuredGitWorkspaces.add(path.resolve(workspacePath));
}

export function isWorkspaceGitConfigured(workspacePath: string): boolean {
  return configuredGitWorkspaces.has(path.resolve(workspacePath));
}

export function logWorkspaceCommit(
  workspacePath: string,
  message: string,
  details?: { stdout?: string; stderr?: string },
) {
  logger.info("Committed workspace changes", {
    workspacePath,
    message,
    ...(details?.stdout ? { stdout: details.stdout } : {}),
    ...(details?.stderr ? { stderr: details.stderr } : {}),
  });
}
