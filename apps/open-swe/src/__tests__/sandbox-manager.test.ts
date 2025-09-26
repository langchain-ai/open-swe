import { describe, expect, test, beforeAll, afterAll } from "@jest/globals";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { execFile as execFileCallback } from "node:child_process";
import { promisify } from "node:util";
import type {
  LocalDockerSandboxOptions,
  WritableMount,
} from "@openswe/sandbox-docker";
import type { SandboxExecOptions, SandboxHandle } from "@openswe/sandbox-core";
import {
  createDockerSandbox,
  deleteSandbox,
  getSandboxMetadata,
  resetSandboxProviderFactory,
  setSandboxProviderFactory,
  stopSandbox,
} from "../utils/sandbox.js";

type ExecResult = SandboxHandle["process"]["executeCommand"] extends (
  ...args: any
) => Promise<infer R>
  ? R
  : never;

class FakeProvider {
  readonly sandboxes = new Map<string, SandboxHandle>();
  readonly executedCommands: string[] = [];
  readonly stopped: string[] = [];
  readonly deleted: string[] = [];
  readonly repoMount?: WritableMount;

  constructor(options: LocalDockerSandboxOptions) {
    this.repoMount = options.writableMounts?.[0];
  }

  async createSandbox(_image: string, mountPath?: string): Promise<SandboxHandle> {
    const id = `fake-${Math.random().toString(36).slice(2)}`;
    const repoPath = this.repoMount?.source ?? mountPath;

    const handle: SandboxHandle = {
      id,
      process: {
        executeCommand: async (command: string): Promise<ExecResult> => {
          this.executedCommands.push(command);
          if (command === "apply-change" && repoPath) {
            await fs.writeFile(
              path.join(repoPath, "README.md"),
              "updated-from-sandbox\n",
            );
          }
          return {
            exitCode: 0,
            stdout: "",
            stderr: "",
            result: "",
            artifacts: { stdout: "", stderr: "" },
          } as ExecResult;
        },
      },
    };

    this.sandboxes.set(id, handle);
    return handle;
  }

  getSandbox(id: string): SandboxHandle | undefined {
    return this.sandboxes.get(id);
  }

  async stopSandbox(id: string): Promise<string> {
    this.stopped.push(id);
    return id;
  }

  async deleteSandbox(id: string): Promise<boolean> {
    this.deleted.push(id);
    this.sandboxes.delete(id);
    return true;
  }

  async exec(
    target: SandboxHandle | string,
    command: string,
    options: SandboxExecOptions = {},
  ): Promise<ExecResult> {
    const handle =
      typeof target === "string" ? this.sandboxes.get(target) : target;

    if (!handle) {
      throw new Error(`Sandbox ${typeof target === "string" ? target : target.id} not found`);
    }

    return await handle.process.executeCommand(
      command,
      options.cwd,
      options.env,
      options.timeoutSec,
    );
  }
}

async function initGitRepository(repoPath: string): Promise<void> {
  await execFile("git", ["init"], { cwd: repoPath });
  await execFile("git", ["config", "user.email", "tester@example.com"], {
    cwd: repoPath,
  });
  await execFile("git", ["config", "user.name", "Open SWE Tester"], {
    cwd: repoPath,
  });
  await fs.writeFile(path.join(repoPath, "README.md"), "initial\n");
  await execFile("git", ["add", "README.md"], { cwd: repoPath });
  await execFile("git", ["commit", "-m", "initial"], { cwd: repoPath });
}

describe("LocalDockerSandboxProvider integration", () => {
  let fakeProvider: FakeProvider | undefined;
  let repoDir: string;

  beforeAll(async () => {
    repoDir = await fs.mkdtemp(path.join(os.tmpdir(), "sandbox-provider-"));
    await initGitRepository(repoDir);
  });

  afterAll(async () => {
    await fs.rm(repoDir, { recursive: true, force: true });
  });

  beforeEach(() => {
    setSandboxProviderFactory((options) => {
      fakeProvider = new FakeProvider(options);
      return fakeProvider;
    });
  });

  afterEach(() => {
    resetSandboxProviderFactory();
    fakeProvider = undefined;
  });

  test("provisions sandbox, executes command, commits, and tears down", async () => {
    const sandbox = await createDockerSandbox("fake-image", {
      hostRepoPath: repoDir,
      repoName: "repo-under-test",
      commitOnChange: true,
      commandTimeoutSec: 5,
    });

    expect(sandbox.id).toBeDefined();
    expect(fakeProvider).toBeDefined();
    expect(fakeProvider?.executedCommands.some((command) =>
      command.includes("safe.directory"),
    )).toBe(true);

    const metadata = getSandboxMetadata(sandbox.id);
    expect(metadata).toBeDefined();
    expect(metadata?.commitOnChange).toBe(true);
    expect(metadata?.containerRepoPath).toBe("/workspace/repo-under-test");

    await sandbox.process.executeCommand("apply-change", metadata?.containerRepoPath);

    const log = await execFile("git", ["log", "-1", "--pretty=%s"], {
      cwd: repoDir,
    });
    expect(log.stdout).toContain("OpenSWE auto-commit");

    const status = await execFile("git", ["status", "--porcelain"], {
      cwd: repoDir,
    });
    expect(status.stdout.trim()).toBe("");

    await stopSandbox(sandbox.id);
    await deleteSandbox(sandbox.id);

    expect(fakeProvider?.stopped).toContain(sandbox.id);
    expect(fakeProvider?.deleted).toContain(sandbox.id);
    expect(getSandboxMetadata(sandbox.id)).toBeUndefined();
  });
});

const execFile = promisify(execFileCallback);
