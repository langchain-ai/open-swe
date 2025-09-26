import process from "node:process";
import { SANDBOX_DOCKER_IMAGE } from "../src/constants.js";
import {
  createDockerSandbox,
  deleteSandbox,
  getSandboxMetadata,
  type Sandbox,
} from "../src/utils/sandbox.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";

const logger = createLogger(LogLevel.INFO, "SmokeScript");

type CommandExecutionResult = {
  exitCode: number;
  stdout: string;
  stderr: string;
  result?: unknown;
};

async function runCommand(
  sandbox: Sandbox,
  command: string,
  cwd?: string,
): Promise<CommandExecutionResult> {
  logger.info("Executing command", { command, cwd });
  const execResult = await sandbox.process.executeCommand(command, cwd);

  const stdout = execResult.stdout ?? "";
  const stderr = execResult.stderr ?? "";

  if (stdout.trim()) {
    logger.info("Command stdout", { command, stdout });
  }

  if (stderr.trim()) {
    logger.warn("Command stderr", { command, stderr });
  }

  const exitCode = execResult.exitCode ?? 0;
  if (exitCode === 0) {
    logger.info("Command completed", { command, exitCode });
  } else {
    logger.warn("Command completed with non-zero exit code", {
      command,
      exitCode,
    });
  }

  return { exitCode, stdout, stderr, result: execResult.result };
}

async function attemptNoOpCommit(
  sandbox: Sandbox,
  cwd: string,
): Promise<void> {
  const commitMessage = "Sandbox smoke test noop";
  const commitResult = await runCommand(
    sandbox,
    `git commit --allow-empty -m "${commitMessage}"`,
    cwd,
  );

  if (commitResult.exitCode === 0) {
    logger.info("No-op commit created successfully, resetting repository");
    await runCommand(sandbox, "git reset --soft HEAD~1", cwd);
  } else {
    logger.warn("No-op commit command failed", {
      exitCode: commitResult.exitCode,
    });
  }
}

async function main(): Promise<void> {
  let sandbox: Sandbox | undefined;

  try {
    sandbox = await createDockerSandbox(SANDBOX_DOCKER_IMAGE, {
      hostRepoPath: process.cwd(),
      commitOnChange: false,
    });
  } catch (error) {
    logger.error("Failed to create sandbox", { error });
    process.exitCode = 1;
    return;
  }

  const sandboxId = sandbox.id;
  const metadata = getSandboxMetadata(sandboxId);
  const cwd = metadata?.containerRepoPath;

  if (!metadata || !cwd) {
    logger.error("Failed to determine sandbox working directory", { metadata });
    await deleteSandbox(sandboxId);
    process.exitCode = 1;
    return;
  }

  try {
    const versionCommands = [
      "python --version",
      "node --version",
      "tsc --version",
      "javac -version",
    ];

    for (const command of versionCommands) {
      await runCommand(sandbox, command, cwd);
    }

    await runCommand(
      sandbox,
      "cat <<'EOF' > smoke-sample.ts\nconst message: string = 'Hello from smoke test';\nconsole.log(message);\nEOF",
      cwd,
    );

    await runCommand(sandbox, "ts-node smoke-sample.ts", cwd);
    await runCommand(sandbox, "rm -f smoke-sample.ts", cwd);

    await runCommand(sandbox, "pytest -q || true", cwd);

    await attemptNoOpCommit(sandbox, cwd);
  } finally {
    await deleteSandbox(sandboxId);
  }
}

main().catch((error) => {
  logger.error("Smoke script failed", { error });
  process.exitCode = 1;
});
