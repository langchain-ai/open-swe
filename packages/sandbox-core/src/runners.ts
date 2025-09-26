import type {
  ExecResult,
  SandboxExecOptions,
  SandboxHandle,
  SandboxProvider,
} from "./types.js";

export type SandboxCommandTarget =
  | SandboxHandle
  | {
      sandbox: SandboxHandle;
      provider?: SandboxProvider;
    }
  | {
      sandboxId: string;
      provider: SandboxProvider;
    };

function isSandboxHandle(target: SandboxCommandTarget | SandboxHandle): target is SandboxHandle {
  return typeof (target as SandboxHandle)?.process?.executeCommand === "function";
}

function resolveProvider(
  target: SandboxCommandTarget,
): {
  sandboxHandle?: SandboxHandle;
  sandboxId: string;
  provider?: SandboxProvider;
} {
  if (isSandboxHandle(target)) {
    return { sandboxHandle: target, sandboxId: target.id };
  }

  if ("sandbox" in target) {
    return {
      sandboxHandle: target.sandbox,
      sandboxId: target.sandbox.id,
      provider: target.provider,
    };
  }

  return {
    sandboxId: target.sandboxId,
    provider: target.provider,
  };
}

export async function execInSandbox(
  target: SandboxCommandTarget,
  command: string,
  options: SandboxExecOptions = {},
): Promise<ExecResult> {
  const { sandboxHandle, sandboxId, provider } = resolveProvider(target);
  if (provider) {
    return await provider.exec(sandboxHandle ?? sandboxId, command, options);
  }

  if (!sandboxHandle) {
    throw new Error(
      "Sandbox handle is required when provider is not supplied to execInSandbox",
    );
  }

  return await sandboxHandle.process.executeCommand(
    command,
    options.cwd,
    options.env,
    options.timeoutSec,
  );
}

type NodePackageManager = "npm" | "yarn" | "pnpm";

export interface InstallNodeDepsOptions extends SandboxExecOptions {
  packageManager?: NodePackageManager;
  frozenLockfile?: boolean;
  additionalArgs?: string[];
  commandOverride?: string;
}

function buildNodeInstallCommand(options: InstallNodeDepsOptions = {}): string {
  const packageManager = options.packageManager ?? "npm";
  const args = options.additionalArgs ? [...options.additionalArgs] : [];

  if (options.commandOverride) {
    return options.commandOverride;
  }

  if (packageManager === "yarn" || packageManager === "pnpm") {
    const command = [packageManager, "install"];
    if (options.frozenLockfile) {
      command.push("--frozen-lockfile");
    }
    command.push(...args);
    return command.join(" ");
  }

  // npm specific handling - use npm ci when frozen lockfiles are desired
  const subcommand = options.frozenLockfile ? "ci" : "install";
  return ["npm", subcommand, ...args].join(" ");
}

export async function installNodeDeps(
  target: SandboxCommandTarget,
  options: InstallNodeDepsOptions = {},
): Promise<ExecResult> {
  const command = buildNodeInstallCommand(options);
  return await execInSandbox(target, command, options);
}

export interface RunPythonTestsOptions extends SandboxExecOptions {
  pythonPath?: string;
  module?: string;
  args?: string[];
  files?: string[];
  commandOverride?: string;
}

function buildPythonTestCommand(options: RunPythonTestsOptions = {}): string {
  if (options.commandOverride) {
    return options.commandOverride;
  }

  const pythonPath = options.pythonPath ?? "python";
  const module = options.module ?? "pytest";
  const args = options.args ? [...options.args] : [];
  const files = options.files ? [...options.files] : [];

  const commandParts = [pythonPath];
  if (module) {
    commandParts.push("-m", module);
  }
  commandParts.push(...args);
  commandParts.push(...files);

  return commandParts.join(" ");
}

export async function runPythonTests(
  target: SandboxCommandTarget,
  options: RunPythonTestsOptions = {},
): Promise<ExecResult> {
  const command = buildPythonTestCommand(options);
  return await execInSandbox(target, command, options);
}

export interface RunJavaTestsOptions extends SandboxExecOptions {
  buildTool?: "maven" | "gradle";
  args?: string[];
  useWrapper?: boolean;
  commandOverride?: string;
}

function buildJavaTestCommand(options: RunJavaTestsOptions = {}): string {
  if (options.commandOverride) {
    return options.commandOverride;
  }

  const buildTool = options.buildTool ?? "maven";
  const args = options.args ? [...options.args] : [];

  if (buildTool === "gradle") {
    const executable = options.useWrapper ?? true ? "./gradlew" : "gradle";
    return [executable, "test", ...args].join(" ");
  }

  return ["mvn", "test", ...args].join(" ");
}

export async function runJavaTests(
  target: SandboxCommandTarget,
  options: RunJavaTestsOptions = {},
): Promise<ExecResult> {
  const command = buildJavaTestCommand(options);
  return await execInSandbox(target, command, options);
}

export type {
  InstallNodeDepsOptions as NodeDepsOptions,
  RunPythonTestsOptions as PythonTestOptions,
  RunJavaTestsOptions as JavaTestOptions,
};
