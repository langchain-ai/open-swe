import Docker from "dockerode";
import Stream from "node:stream";
import { createLogger, LogLevel } from "./logger.js";
import { SANDBOX_DOCKER_IMAGE } from "../constants.js";
import { GraphConfig, TargetRepository } from "@openswe/shared/open-swe/types";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@openswe/shared/open-swe/local-mode";
import { uploadRepoToContainer } from "@openswe/shared/upload-repo-to-container";

const logger = createLogger(LogLevel.INFO, "Sandbox");

function parsePositiveInt(
  envValue: string | undefined,
  fallback: number,
): number {
  if (!envValue) return fallback;
  const parsed = Number.parseInt(envValue, 10);
  return Number.isNaN(parsed) || parsed <= 0 ? fallback : parsed;
}

/**
 * Defaults favor isolation: cap resources, drop root, and block networking. Operators can relax
 * limits via OPEN_SWE_SANDBOX_MEMORY_BYTES, OPEN_SWE_SANDBOX_NANO_CPUS,
 * OPEN_SWE_SANDBOX_USER, and OPEN_SWE_SANDBOX_ENABLE_NETWORK when needed.
 */
const SANDBOX_MEMORY_LIMIT_BYTES = parsePositiveInt(
  process.env.OPEN_SWE_SANDBOX_MEMORY_BYTES,
  2 * 1024 * 1024 * 1024,
);
const SANDBOX_NANO_CPUS = parsePositiveInt(
  process.env.OPEN_SWE_SANDBOX_NANO_CPUS,
  1_000_000_000,
);
const SANDBOX_USER = process.env.OPEN_SWE_SANDBOX_USER?.trim() || "node";
const SANDBOX_NETWORK_DISABLED =
  process.env.OPEN_SWE_SANDBOX_ENABLE_NETWORK?.trim().toLowerCase() === "true"
    ? false
    : true;

let docker: Docker | null = null;
let dockerInitializationPromise: Promise<Docker> | null = null;

async function dockerClient(): Promise<Docker> {
  if (docker) return docker;

  if (!dockerInitializationPromise) {
    dockerInitializationPromise = (async () => {
      const client = new Docker();
      try {
        await client.ping();
      } catch (error) {
        const detail =
          error instanceof Error
            ? error.message
            : typeof error === "string"
              ? error
              : "";
        const suffix =
          detail && detail !== "[object Object]" ? ` Details: ${detail}` : "";
        throw new Error(
          `Docker daemon not running or unreachable. Please start Docker and try again.${suffix}`,
        );
      }
      docker = client;
      return client;
    })();
  }

  try {
    const client = await dockerInitializationPromise;
    dockerInitializationPromise = null;
    return client;
  } catch (error) {
    dockerInitializationPromise = null;
    throw error;
  }
}

export interface SandboxProcess {
  executeCommand(
    command: string,
    cwd?: string,
    env?: Record<string, string>,
    timeoutSec?: number,
  ): Promise<{
    exitCode: number;
    stdout: string;
    stderr: string;
    result: string;
    artifacts?: { stdout: string; stderr: string };
  }>;
}

export interface Sandbox {
  id: string;
  process: SandboxProcess;
}

function isMissingImageError(error: unknown): boolean {
  if (!error) return false;

  if (typeof error === "object" && error && "statusCode" in error) {
    const statusCode = (error as { statusCode?: number }).statusCode;
    if (statusCode === 404) return true;
  }

  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
        ? error
        : "";

  return message.toLowerCase().includes("no such image");
}

async function ensureImageAvailable(
  docker: Docker,
  image: string,
): Promise<void> {
  try {
    await docker.getImage(image).inspect();
    return;
  } catch (error) {
    if (!isMissingImageError(error)) {
      throw error;
    }
  }

  await new Promise<void>((resolve, reject) => {
    docker.pull(
      image,
      (pullError: unknown, stream: NodeJS.ReadableStream | undefined) => {
        if (pullError) {
          const detail =
            pullError instanceof Error
              ? pullError.message
              : typeof pullError === "string"
                ? pullError
                : "";
          reject(
            new Error(
              `Failed to pull Docker image "${image}". Please ensure the image exists and is accessible.${
                detail && detail !== "[object Object]"
                  ? ` Details: ${detail}`
                  : ""
              }`,
            ),
          );
          return;
        }

        if (!stream) {
          reject(
            new Error(
              `Failed to pull Docker image "${image}". Docker did not return a pull progress stream.`,
            ),
          );
          return;
        }

        docker.modem.followProgress(stream, (progressError?: unknown) => {
          if (progressError) {
            const detail =
              progressError instanceof Error
                ? progressError.message
                : typeof progressError === "string"
                  ? progressError
                  : "";
            reject(
              new Error(
                `Failed to pull Docker image "${image}". Please ensure the image exists and is accessible.${
                  detail && detail !== "[object Object]"
                    ? ` Details: ${detail}`
                    : ""
                }`,
              ),
            );
            return;
          }

          resolve();
        });
      },
    );
  });
}

const sandboxes = new Map<string, Sandbox>();
export function getSandbox(id: string): Sandbox | undefined {
  return sandboxes.get(id);
}

export async function createDockerSandbox(
  image: string,
  mountPath = "/workspace",
): Promise<Sandbox> {
  const docker = await dockerClient();
  await ensureImageAvailable(docker, image);
  const hostConfig: Docker.ContainerCreateOptions["HostConfig"] = {
    Memory: SANDBOX_MEMORY_LIMIT_BYTES,
  };

  if (SANDBOX_NANO_CPUS > 0) {
    (
      hostConfig as Docker.ContainerCreateOptions["HostConfig"] & {
        NanoCPUs?: number;
      }
    ).NanoCPUs = SANDBOX_NANO_CPUS;
  }

  const container = await docker.createContainer({
    Image: image,
    Tty: true,
    Cmd: ["/bin/sh", "-c", "while true; do sleep 3600; done"],
    Env: [`SANDBOX_ROOT_DIR=${mountPath}`],
    HostConfig: hostConfig,
    NetworkDisabled: SANDBOX_NETWORK_DISABLED,
    User: SANDBOX_USER,
  });
  await container.start();

  const process: SandboxProcess = {
    async executeCommand(command, cwd = mountPath, env, timeoutSec) {
      const exec = await container.exec({
        Cmd: ["bash", "-lc", command],
        AttachStdout: true,
        AttachStderr: true,
        WorkingDir: cwd,
        Env: env ? Object.entries(env).map(([k, v]) => `${k}=${v}`) : undefined,
      });
      const stream = await exec.start({});
      let stdout = "";
      let stderr = "";
      const stdoutStream = new Stream.PassThrough();
      const stderrStream = new Stream.PassThrough();
      docker.modem.demuxStream(stream, stdoutStream, stderrStream);
      stdoutStream.on("data", (d) => {
        stdout += d.toString();
      });
      stderrStream.on("data", (d) => {
        stderr += d.toString();
      });
      await new Promise<void>((resolve, reject) => {
        const timer =
          timeoutSec !== undefined
            ? setTimeout(() => {
                stream.destroy(new Error("Command timed out"));
              }, timeoutSec * 1000)
            : null;
        stream.on("end", () => {
          if (timer) clearTimeout(timer);
          resolve();
        });
        stream.on("error", (err) => {
          if (timer) clearTimeout(timer);
          reject(err);
        });
      });
      const inspect = await exec.inspect();
      return {
        exitCode: inspect.ExitCode ?? -1,
        stdout,
        stderr,
        result: stdout,
        artifacts: { stdout, stderr },
      };
    },
  };

  const sandbox: Sandbox = { id: container.id, process };
  sandboxes.set(sandbox.id, sandbox);
  return sandbox;
}

export async function stopSandbox(sandboxId: string): Promise<string> {
  const docker = await dockerClient();
  const container = docker.getContainer(sandboxId);
  try {
    await container.stop();
  } catch {
    /* ignore */
  }
  return sandboxId;
}

export async function deleteSandbox(sandboxId: string): Promise<boolean> {
  const docker = await dockerClient();
  const container = docker.getContainer(sandboxId);
  try {
    await container.remove({ force: true });
    sandboxes.delete(sandboxId);
    return true;
  } catch (error) {
    logger.error("Failed to delete sandbox", { sandboxId, error });
    return false;
  }
}

export async function getSandboxWithErrorHandling(
  sandboxSessionId: string | undefined,
  _targetRepository: TargetRepository,
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
  const image = SANDBOX_DOCKER_IMAGE;
  const repoPath = getLocalWorkingDirectory();
  const sandbox = await createDockerSandbox(image);
  await uploadRepoToContainer({
    containerId: sandbox.id,
    localRepoPath: repoPath,
  });
  return { sandbox, codebaseTree: null, dependenciesInstalled: null };
}
