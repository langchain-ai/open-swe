import Docker from "dockerode";
import { randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { PassThrough } from "node:stream";
import type { Container } from "dockerode";
import type {
  ExecResult,
  SandboxExecOptions,
  SandboxHandle,
  SandboxProvider,
  SandboxResourceLimits,
} from "@openswe/sandbox-core";

const DEFAULT_USER = "1000:1000";
const DEFAULT_TMPFS_OPTS = "rw,nosuid,nodev,noexec,size=64m";
const DEFAULT_WORKING_DIR = "/workspace/src";
const DEFAULT_CPU_COUNT = 2;
const DEFAULT_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024;
const DEFAULT_EXEC_TIMEOUT_SEC = 900;
const DEFAULT_PIDS_LIMIT = 512;
const SECURITY_VIOLATION_EXIT_CODE = 126;
const TIMEOUT_EXIT_CODE = 124;

class CommandTimeoutError extends Error {
  constructor(readonly seconds: number) {
    super(`Command timed out after ${seconds} seconds`);
    this.name = "CommandTimeoutError";
  }
}

export interface WritableMount {
  source: string;
  target: string;
}

export interface LocalDockerSandboxResources {
  cpuCount?: number;
  memoryBytes?: number;
  networkDisabled?: boolean;
  networkMode?: string;
  pidsLimit?: number;
}

export interface LocalDockerSandboxOptions {
  dockerOptions?: Docker.DockerOptions;
  defaultMountPath?: string;
  writableMounts?: WritableMount[];
  user?: string;
  tmpfsOptions?: string;
  resources?: LocalDockerSandboxResources;
  workingDirectory?: string;
  ensureMountsExist?: boolean;
  defaultTimeoutSec?: number;
  containerName?: string;
}

type InternalSandboxRecord = {
  container: Container;
  handle: SandboxHandle;
};

export class LocalDockerSandboxProvider implements SandboxProvider {
  private readonly docker: Docker;
  private readonly sandboxes = new Map<string, InternalSandboxRecord>();
  private readonly options: LocalDockerSandboxOptions;

  constructor(options: LocalDockerSandboxOptions = {}) {
    this.docker = new Docker(options.dockerOptions);
    this.options = options;
  }

  async createSandbox(image: string, mountPath?: string): Promise<SandboxHandle> {
    const repositoryPath = await this.resolveMountPath(mountPath);
    await this.ensurePathExists(repositoryPath, false);

    const writableMounts = this.options.writableMounts ?? [];
    const ensureWritable = this.options.ensureMountsExist ?? true;
    await Promise.all(
      writableMounts.map((mount) => this.ensurePathExists(mount.source, ensureWritable)),
    );

    const binds = [
      `${repositoryPath}:/workspace/src:ro`,
      ...writableMounts.map((mount) => `${mount.source}:${mount.target}:rw`),
    ];

    const networkMode = this.options.resources?.networkDisabled
      ? "none"
      : this.options.resources?.networkMode ?? "bridge";

    const requestedCpuCount = this.options.resources?.cpuCount;
    const effectiveCpuCount =
      requestedCpuCount && requestedCpuCount > 0
        ? requestedCpuCount
        : DEFAULT_CPU_COUNT;
    const nanoCpus = Math.floor(effectiveCpuCount * 1_000_000_000);

    const requestedMemory = this.options.resources?.memoryBytes;
    const memoryBytes =
      requestedMemory && requestedMemory > 0
        ? requestedMemory
        : DEFAULT_MEMORY_LIMIT_BYTES;

    const requestedPidsLimit = this.options.resources?.pidsLimit;
    const pidsLimit =
      requestedPidsLimit && requestedPidsLimit > 0
        ? requestedPidsLimit
        : DEFAULT_PIDS_LIMIT;

    const hostConfig: Docker.ContainerCreateOptions["HostConfig"] = {
      Binds: binds,
      Tmpfs: {
        "/tmp": this.options.tmpfsOptions ?? DEFAULT_TMPFS_OPTS,
      },
      CapDrop: ["ALL"],
      SecurityOpt: ["no-new-privileges"],
      NetworkMode: networkMode,
      NanoCpus: nanoCpus,
      Memory: memoryBytes,
      ReadonlyRootfs: true,
      PidsLimit: pidsLimit,
    };

    const createOpts: Docker.ContainerCreateOptions = {
      name: this.options.containerName,
      Image: image,
      Cmd: ["/bin/sh", "-c", "tail -f /dev/null"],
      Tty: false,
      OpenStdin: false,
      AttachStderr: false,
      AttachStdout: false,
      WorkingDir: this.options.workingDirectory ?? DEFAULT_WORKING_DIR,
      User: this.options.user ?? DEFAULT_USER,
      HostConfig: hostConfig,
      Labels: {
        "openswe.sandbox": "local-docker",
      },
    };

    // eslint-disable-next-line no-console
    console.log("[Sandbox] Docker HostConfig.Binds =", createOpts?.HostConfig?.Binds);

    const container = await this.docker.createContainer(createOpts);

    await container.start();

    const inspectData = await container.inspect();
    const id = container.id ?? randomUUID();
    const requestedResources: SandboxResourceLimits = {
      cpuCount: effectiveCpuCount,
      memoryBytes,
      pidsLimit,
    };
    const appliedResources: SandboxResourceLimits = {
      cpuCount:
        typeof inspectData.HostConfig?.NanoCpus === "number" &&
        inspectData.HostConfig.NanoCpus > 0
          ? inspectData.HostConfig.NanoCpus / 1_000_000_000
          : undefined,
      memoryBytes:
        typeof inspectData.HostConfig?.Memory === "number" &&
        inspectData.HostConfig.Memory > 0
          ? inspectData.HostConfig.Memory
          : undefined,
      pidsLimit:
        typeof inspectData.HostConfig?.PidsLimit === "number" &&
        inspectData.HostConfig.PidsLimit > 0
          ? inspectData.HostConfig.PidsLimit
          : undefined,
    };
    const handle: SandboxHandle = {
      id,
      metadata: {
        containerId: id,
        containerName:
          inspectData.Name?.replace(/^\//, "") ?? this.options.containerName,
        requestedResources,
        appliedResources,
      },
      process: {
        executeCommand: async (
          command: string,
          cwd?: string,
          env?: Record<string, string>,
          timeoutSec?: number,
        ) => this.executeCommand(container, command, cwd, env, timeoutSec),
      },
    };

    this.sandboxes.set(id, { container, handle });
    return handle;
  }

  getSandbox(id: string): SandboxHandle | undefined {
    return this.sandboxes.get(id)?.handle;
  }

  async stopSandbox(id: string): Promise<string> {
    const record = this.sandboxes.get(id);
    if (!record) {
      throw new Error(`Sandbox ${id} not found`);
    }

    await this.safeStop(record.container);
    return id;
  }

  async deleteSandbox(id: string): Promise<boolean> {
    const record = this.sandboxes.get(id);
    if (!record) {
      return false;
    }

    await this.safeStop(record.container);
    await this.safeRemove(record.container);
    this.sandboxes.delete(id);
    return true;
  }

  async exec(
    target: SandboxHandle | string,
    command: string,
    options: SandboxExecOptions = {},
  ): Promise<ExecResult> {
    const id = typeof target === "string" ? target : target.id;
    const record = this.sandboxes.get(id);
    if (!record) {
      throw new Error(`Sandbox ${id} not found`);
    }

    return await this.executeCommand(
      record.container,
      command,
      options.cwd,
      options.env,
      options.timeoutSec,
    );
  }

  private async executeCommand(
    container: Container,
    command: string,
    cwd?: string,
    env?: Record<string, string>,
    timeoutSec?: number,
  ): Promise<ExecResult> {
    const workingDir = cwd ?? this.options.workingDirectory ?? DEFAULT_WORKING_DIR;
    const formattedEnv = env ? Object.entries(env).map(([key, value]) => `${key}=${value}`) : undefined;

    const exec = await container.exec({
      Cmd: ["/bin/sh", "-lc", command],
      WorkingDir: workingDir,
      Env: formattedEnv,
      AttachStdout: true,
      AttachStderr: true,
      AttachStdin: false,
    });

    const execPromise = this.collectExecResult(exec).catch((error) => {
      if (this.isSecurityViolationError(error)) {
        return this.buildFailureResult(
          SECURITY_VIOLATION_EXIT_CODE,
          this.normalizeErrorMessage(error),
        );
      }
      throw error instanceof Error ? error : new Error(String(error));
    });

    const effectiveTimeout = this.resolveTimeout(timeoutSec);

    if (!effectiveTimeout) {
      return execPromise;
    }

    const timeoutMs = effectiveTimeout * 1000;
    let timeoutHandle: NodeJS.Timeout | undefined;

    const timeoutPromise = new Promise<ExecResult>((_, reject) => {
      timeoutHandle = setTimeout(async () => {
        try {
          await container.kill({ signal: "SIGKILL" });
        } catch (error) {
          if (!this.isContainerNotRunningError(error)) {
            reject(error instanceof Error ? error : new Error(String(error)));
            return;
          }
        }
        reject(new CommandTimeoutError(effectiveTimeout));
      }, timeoutMs);
    });

    try {
      const result = await Promise.race([execPromise, timeoutPromise]);
      if (timeoutHandle) {
        clearTimeout(timeoutHandle);
      }
      return result;
    } catch (error) {
      if (timeoutHandle) {
        clearTimeout(timeoutHandle);
      }

      if (error instanceof CommandTimeoutError) {
        void execPromise.catch(() => undefined);
        await this.safeWaitForContainer(container);
        await this.safeStart(container);
        return this.buildFailureResult(TIMEOUT_EXIT_CODE, error.message);
      }

      throw error;
    }
  }

  private resolveTimeout(timeoutSec?: number): number | undefined {
    const defaultTimeout = this.options.defaultTimeoutSec ?? DEFAULT_EXEC_TIMEOUT_SEC;
    const candidate = timeoutSec ?? defaultTimeout;
    if (!candidate || candidate <= 0) {
      return undefined;
    }
    return candidate;
  }

  private async collectExecResult(exec: Docker.Exec): Promise<ExecResult> {
    const stream = await exec.start({ hijack: true, stdin: false });
    const stdoutStream = new PassThrough();
    const stderrStream = new PassThrough();

    const stdoutChunks: Buffer[] = [];
    const stderrChunks: Buffer[] = [];

    stdoutStream.on("data", (chunk) => stdoutChunks.push(Buffer.from(chunk)));
    stderrStream.on("data", (chunk) => stderrChunks.push(Buffer.from(chunk)));

    this.docker.modem.demuxStream(stream, stdoutStream, stderrStream);

    await new Promise<void>((resolve, reject) => {
      stream.on("end", resolve);
      stream.on("close", resolve);
      stream.on("error", (error) => reject(error));
    });

    stdoutStream.end();
    stderrStream.end();

    const inspectData = await exec.inspect();
    const exitCode = inspectData.ExitCode ?? 0;

    const stdout = Buffer.concat(stdoutChunks).toString("utf-8");
    const stderr = Buffer.concat(stderrChunks).toString("utf-8");

    return {
      exitCode,
      stdout,
      stderr,
      result: stdout.trim(),
      artifacts: {
        stdout,
        stderr,
      },
    };
  }

  private isSecurityViolationError(error: unknown): boolean {
    const message = this.normalizeErrorMessage(error).toLowerCase();
    if (!message) {
      return false;
    }

    return (
      message.includes("operation not permitted") ||
      message.includes("permission denied") ||
      message.includes("read-only file system") ||
      message.includes("apparmor") ||
      message.includes("seccomp") ||
      message.includes("security profile") ||
      message.includes("no new privileges") ||
      message.includes("capability")
    );
  }

  private normalizeErrorMessage(error: unknown): string {
    if (!error) {
      return "";
    }
    if (error instanceof Error) {
      return error.message || error.toString();
    }
    return String(error);
  }

  private buildFailureResult(exitCode: number, stderr: string): ExecResult {
    return {
      exitCode,
      stdout: "",
      stderr,
      result: "",
      artifacts: {
        stdout: "",
        stderr,
      },
    };
  }

  private async resolveMountPath(mountPath?: string): Promise<string> {
    const basePath = mountPath ?? this.options.defaultMountPath;
    if (!basePath) {
      throw new Error("A mount path must be provided either explicitly or via defaultMountPath");
    }
    return path.resolve(basePath);
  }

  private async ensurePathExists(targetPath: string, createIfMissing: boolean): Promise<void> {
    try {
      await fs.access(targetPath);
    } catch (error) {
      if (createIfMissing) {
        await fs.mkdir(targetPath, { recursive: true });
        return;
      }
      throw new Error(`Mount path does not exist: ${targetPath}`);
    }
  }

  private isContainerNotRunningError(error: unknown): boolean {
    if (!error) {
      return false;
    }
    const message = error instanceof Error ? error.message : String(error);
    return message.includes("not running") || message.includes("is not running");
  }

  private async safeStop(container: Container): Promise<void> {
    try {
      await container.stop({ t: 0 });
    } catch (error) {
      if (!this.isContainerNotRunningError(error)) {
        throw error;
      }
    }
  }

  private async safeRemove(container: Container): Promise<void> {
    try {
      await container.remove({ force: true });
    } catch (error) {
      if (error instanceof Error && error.message.includes("No such container")) {
        return;
      }
      throw error;
    }
  }

  private async safeWaitForContainer(container: Container): Promise<void> {
    try {
      await container.wait({ condition: "not-running" });
    } catch (error) {
      if (!this.isContainerNotRunningError(error)) {
        throw error;
      }
    }
  }

  private async safeStart(container: Container): Promise<void> {
    try {
      await container.start();
    } catch (error) {
      if (error instanceof Error && error.message.includes("is already running")) {
        return;
      }
      throw error;
    }
  }
}
