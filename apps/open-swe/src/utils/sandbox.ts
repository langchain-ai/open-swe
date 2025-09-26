import path from "node:path";
import { execFile as execFileCallback } from "node:child_process";
import { promisify } from "node:util";
import { randomUUID } from "node:crypto";
import { LocalDockerSandboxProvider } from "@openswe/sandbox-docker";
import type {
  LocalDockerSandboxOptions,
  LocalDockerSandboxResources,
  WritableMount,
} from "@openswe/sandbox-docker";
import type { SandboxHandle, SandboxProvider } from "@openswe/sandbox-core";
import { GraphConfig, TargetRepository } from "@openswe/shared/open-swe/types";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@openswe/shared/open-swe/local-mode";
import { SANDBOX_DOCKER_IMAGE } from "../constants.js";
import { createLogger, LogLevel } from "./logger.js";

const logger = createLogger(LogLevel.INFO, "Sandbox");

type SandboxProviderFactory = (
  options: LocalDockerSandboxOptions,
) => SandboxProvider;

const DEFAULT_REPO_ROOT = "/workspace";
const DEFAULT_COMMIT_MESSAGE = "OpenSWE auto-commit";
const DEFAULT_COMMIT_AUTHOR_NAME = "Open SWE";
const DEFAULT_COMMIT_AUTHOR_EMAIL = "opensource@langchain.dev";
const DEFAULT_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024;

const commitCounters = new Map<string, number>();

function parsePositiveInt(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isNaN(parsed) || parsed <= 0 ? fallback : parsed;
}

function parsePositiveFloat(
  value: string | undefined,
  fallback: number | undefined,
): number | undefined {
  if (!value) return fallback;
  const parsed = Number.parseFloat(value);
  return Number.isNaN(parsed) || parsed <= 0 ? fallback : parsed;
}

function parseBoolean(value: string | undefined, fallback: boolean): boolean {
  if (!value) return fallback;
  const normalized = value.trim().toLowerCase();
  if (["true", "1", "yes", "y"].includes(normalized)) {
    return true;
  }
  if (["false", "0", "no", "n"].includes(normalized)) {
    return false;
  }
  return fallback;
}

const SANDBOX_MEMORY_LIMIT_BYTES = parsePositiveInt(
  process.env.LOCAL_SANDBOX_MEMORY,
  DEFAULT_MEMORY_LIMIT_BYTES,
);

const SANDBOX_CPU_COUNT = parsePositiveFloat(
  process.env.LOCAL_SANDBOX_CPUS,
  undefined,
);

const rawNetworkSetting = process.env.LOCAL_SANDBOX_NETWORK?.trim();
const normalizedNetworkSetting = rawNetworkSetting?.toLowerCase();
const SANDBOX_NETWORK_ENABLED = Boolean(
  rawNetworkSetting &&
    !["none", "false", "off", "disable", "disabled"].includes(
      normalizedNetworkSetting ?? "",
    ),
);
const SANDBOX_NETWORK_MODE = SANDBOX_NETWORK_ENABLED
  ? rawNetworkSetting
  : undefined;

const SANDBOX_COMMAND_TIMEOUT_SEC = parsePositiveInt(
  process.env.LOCAL_SANDBOX_TIMEOUT_SEC,
  60,
);

const COMMIT_AUTHOR_NAME =
  process.env.GIT_AUTHOR_NAME?.trim() || DEFAULT_COMMIT_AUTHOR_NAME;
const COMMIT_AUTHOR_EMAIL =
  process.env.GIT_AUTHOR_EMAIL?.trim() || DEFAULT_COMMIT_AUTHOR_EMAIL;

const COMMIT_COMMITTER_NAME =
  process.env.GIT_COMMITTER_NAME?.trim() || COMMIT_AUTHOR_NAME;
const COMMIT_COMMITTER_EMAIL =
  process.env.GIT_COMMITTER_EMAIL?.trim() || COMMIT_AUTHOR_EMAIL;

const SANDBOX_GIT_USER_NAME = COMMIT_COMMITTER_NAME;
const SANDBOX_GIT_USER_EMAIL = COMMIT_COMMITTER_EMAIL;

const SKIP_CI_UNTIL_LAST_COMMIT = parseBoolean(
  process.env.SKIP_CI_UNTIL_LAST_COMMIT,
  true,
);

function buildCommitMessage(repoPath: string): string {
  const count = (commitCounters.get(repoPath) ?? 0) + 1;
  commitCounters.set(repoPath, count);
  const suffix = SKIP_CI_UNTIL_LAST_COMMIT ? " [skip ci]" : "";
  return `${DEFAULT_COMMIT_MESSAGE} #${count}${suffix}`;
}

async function runGitCommand(
  args: string[],
  cwd: string,
): Promise<{ stdout: string; stderr: string }> {
  const env = {
    ...process.env,
    GIT_AUTHOR_NAME: COMMIT_AUTHOR_NAME,
    GIT_AUTHOR_EMAIL: COMMIT_AUTHOR_EMAIL,
    GIT_COMMITTER_NAME: COMMIT_COMMITTER_NAME,
    GIT_COMMITTER_EMAIL: COMMIT_COMMITTER_EMAIL,
  };

  try {
    const { stdout, stderr } = await execFile("git", args, { cwd, env });
    return { stdout: stdout.toString(), stderr: stderr.toString() };
  } catch (error) {
    if (error && typeof error === "object" && "stdout" in error) {
      const stdout = String((error as { stdout?: string }).stdout ?? "");
      const stderr = String((error as { stderr?: string }).stderr ?? "");
      return { stdout, stderr };
    }
    throw error;
  }
}

async function commitHostChanges(repoPath: string): Promise<void> {
  try {
    const status = await runGitCommand(["status", "--porcelain"], repoPath);
    if (!status.stdout.trim()) {
      return;
    }

    await runGitCommand(["add", "--all"], repoPath);
    const message = buildCommitMessage(repoPath);
    const commitResult = await runGitCommand(
      ["commit", "-m", message],
      repoPath,
    );

    if (commitResult.stderr.trim()) {
      logger.info("Git commit completed with messages", {
        repoPath,
        stderr: commitResult.stderr,
      });
    }

    logger.info("Committed sandbox changes", { repoPath, message });
  } catch (error) {
    logger.error("Failed to commit sandbox changes", {
      repoPath,
      error,
    });
  }
}

async function configureContainerGit(
  handle: SandboxHandle,
  repoPath: string,
): Promise<void> {
  try {
    await handle.process.executeCommand(
      `git config --global --add safe.directory ${repoPath}`,
      repoPath,
    );
    if (SANDBOX_GIT_USER_NAME) {
      await handle.process.executeCommand(
        `git config --global user.name "${SANDBOX_GIT_USER_NAME}"`,
        repoPath,
      );
    }
    if (SANDBOX_GIT_USER_EMAIL) {
      await handle.process.executeCommand(
        `git config --global user.email "${SANDBOX_GIT_USER_EMAIL}"`,
        repoPath,
      );
    }
  } catch (error) {
    logger.warn("Failed to configure git inside sandbox", {
      repoPath,
      error,
    });
  }
}

let sandboxProviderFactory: SandboxProviderFactory = (options) =>
  new LocalDockerSandboxProvider(options);

export function setSandboxProviderFactory(
  factory: SandboxProviderFactory,
): void {
  sandboxProviderFactory = factory;
}

export function resetSandboxProviderFactory(): void {
  sandboxProviderFactory = (options) => new LocalDockerSandboxProvider(options);
}

export type SandboxProcess = SandboxHandle["process"];
export type Sandbox = SandboxHandle;

interface SandboxMetadata {
  provider: SandboxProvider;
  hostRepoPath?: string;
  containerRepoPath: string;
  commitOnChange: boolean;
  commandTimeoutSec: number;
}

const sandboxes = new Map<string, Sandbox>();
const sandboxMetadata = new Map<string, SandboxMetadata>();

export function getSandbox(id: string): Sandbox | undefined {
  return sandboxes.get(id);
}

export function getSandboxMetadata(id: string): SandboxMetadata | undefined {
  return sandboxMetadata.get(id);
}

function resolveRepoName(hostRepoPath?: string, provided?: string): string {
  if (provided) return provided;
  if (hostRepoPath) {
    const normalized = path.resolve(hostRepoPath);
    return path.basename(normalized) || `sandbox-${randomUUID()}`;
  }
  return `sandbox-${randomUUID()}`;
}

export interface CreateSandboxOptions {
  hostRepoPath?: string;
  repoName?: string;
  containerRepoPath?: string;
  commitOnChange?: boolean;
  commandTimeoutSec?: number;
}

export async function createDockerSandbox(
  image: string,
  options: CreateSandboxOptions = {},
): Promise<Sandbox> {
  const hostRepoPath = options.hostRepoPath;
  const repoName = resolveRepoName(hostRepoPath, options.repoName);
  const containerRepoPath =
    options.containerRepoPath ?? path.join(DEFAULT_REPO_ROOT, repoName);
  const commitOnChange = options.commitOnChange ?? false;
  const commandTimeoutSec =
    options.commandTimeoutSec ?? SANDBOX_COMMAND_TIMEOUT_SEC;

  const writableMounts: WritableMount[] | undefined = commitOnChange && hostRepoPath
    ? [{ source: path.resolve(hostRepoPath), target: containerRepoPath }]
    : undefined;

  const resources: LocalDockerSandboxResources = {
    cpuCount: SANDBOX_CPU_COUNT,
    memoryBytes: SANDBOX_MEMORY_LIMIT_BYTES,
    networkDisabled: !SANDBOX_NETWORK_ENABLED,
    networkMode: SANDBOX_NETWORK_MODE,
  };

  const providerOptions: LocalDockerSandboxOptions = {
    defaultMountPath: hostRepoPath ? path.resolve(hostRepoPath) : process.cwd(),
    writableMounts,
    resources,
    workingDirectory: containerRepoPath,
    ensureMountsExist: true,
  };

  const provider = sandboxProviderFactory(providerOptions);
  const handle = await provider.createSandbox(image, hostRepoPath);

  await configureContainerGit(handle, containerRepoPath);

  const sandboxProcess: SandboxProcess = {
    async executeCommand(command, cwd, env, timeoutSec) {
      const effectiveCwd = cwd ?? containerRepoPath;
      const effectiveTimeout = timeoutSec ?? commandTimeoutSec;
      const result = await handle.process.executeCommand(
        command,
        effectiveCwd,
        env,
        effectiveTimeout,
      );

      if (commitOnChange && hostRepoPath && result.exitCode === 0) {
        await commitHostChanges(hostRepoPath);
      }

      return result;
    },
  };

  const sandbox: Sandbox = { id: handle.id, process: sandboxProcess };
  sandboxes.set(sandbox.id, sandbox);
  sandboxMetadata.set(sandbox.id, {
    provider,
    hostRepoPath: hostRepoPath ? path.resolve(hostRepoPath) : undefined,
    containerRepoPath,
    commitOnChange,
    commandTimeoutSec,
  });

  return sandbox;
}

export async function stopSandbox(sandboxId: string): Promise<string> {
  const metadata = sandboxMetadata.get(sandboxId);
  if (metadata?.commitOnChange && metadata.hostRepoPath) {
    await commitHostChanges(metadata.hostRepoPath);
  }

  if (metadata) {
    try {
      await metadata.provider.stopSandbox(sandboxId);
    } catch (error) {
      logger.warn("Failed to stop sandbox", { sandboxId, error });
    }
  }

  return sandboxId;
}

export async function deleteSandbox(sandboxId: string): Promise<boolean> {
  const metadata = sandboxMetadata.get(sandboxId);
  if (metadata?.commitOnChange && metadata.hostRepoPath) {
    await commitHostChanges(metadata.hostRepoPath);
  }

  if (!metadata) {
    return false;
  }

  try {
    const deleted = await metadata.provider.deleteSandbox(sandboxId);
    if (deleted) {
      sandboxes.delete(sandboxId);
      sandboxMetadata.delete(sandboxId);
    }
    return deleted;
  } catch (error) {
    logger.error("Failed to delete sandbox", { sandboxId, error });
    return false;
  }
}

export async function getSandboxWithErrorHandling(
  sandboxSessionId: string | undefined,
  targetRepository: TargetRepository,
  _branchName: string,
  config: GraphConfig,
): Promise<{
  sandbox: Sandbox;
  codebaseTree: string | null;
  dependenciesInstalled: boolean | null;
}> {
  if (!isLocalMode(config)) {
    throw new Error("Sandbox operations are only supported in local mode");
  }

  if (sandboxSessionId) {
    const existing = getSandbox(sandboxSessionId);
    if (existing) {
      return {
        sandbox: existing,
        codebaseTree: null,
        dependenciesInstalled: null,
      };
    }
  }

  const repoPath = getLocalWorkingDirectory();
  const sandbox = await createDockerSandbox(SANDBOX_DOCKER_IMAGE, {
    hostRepoPath: repoPath,
    repoName: targetRepository.repo,
    commitOnChange: true,
  });

  return { sandbox, codebaseTree: null, dependenciesInstalled: null };
}

const execFile = promisify(execFileCallback);
