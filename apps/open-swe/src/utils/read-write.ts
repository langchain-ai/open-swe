import { Sandbox } from "@e2b/code-interpreter";
import { createLogger, LogLevel } from "./logger.js";
import { TIMEOUT_MS } from "../constants.js";
import { getSandboxErrorFields } from "./sandbox-error-fields.js";
import { traceable } from "langsmith/traceable";

const logger = createLogger(LogLevel.INFO, "ReadWriteUtil");

async function readFileFunc(
  sandbox: Sandbox,
  filePath: string,
  args?: {
    workDir?: string;
  },
): Promise<{
  success: boolean;
  output: string;
}> {
  try {
    const readOutput = await sandbox.commands.run(`cat "${filePath}"`, {
      cwd: args?.workDir,
    });
    // Add an extra 5 min timeout to the sandbox.
    await sandbox.setTimeout(TIMEOUT_MS);

    if (readOutput.exitCode !== 0) {
      logger.error(`Error reading file '${filePath}' from sandbox via cat:`, {
        readOutput,
      });
      return {
        success: false,
        output: `FAILED TO READ FILE from sandbox '${filePath}'. Exit code: ${readOutput.exitCode}.\nStderr: ${readOutput.stderr}.\nStdout: ${readOutput.stdout}`,
      };
    }
    if (readOutput.stderr) {
      logger.warn(
        `Stderr while reading file '${filePath}' from sandbox via cat: ${readOutput.stderr}`,
      );
    }
    return {
      success: true,
      output: readOutput.stdout,
    };
  } catch (e: any) {
    logger.error(
      `Exception while trying to read file '${filePath}' from sandbox via cat:`,
      {
        ...(e instanceof Error
          ? { name: e.name, message: e.message, stack: e.stack }
          : { error: e }),
      },
    );
    let outputMessage = `FAILED TO EXECUTE READ COMMAND for sandbox '${filePath}'.`;
    const errorFields = getSandboxErrorFields(e);
    if (errorFields) {
      outputMessage += `\nExit code: ${errorFields.exitCode}.\nStderr: ${errorFields.stderr}.\nStdout: ${errorFields.stdout}`;
    } else {
      outputMessage += ` Error: ${(e as Error).message || String(e)}`;
    }

    return {
      success: false,
      output: outputMessage,
    };
  }
}

export const readFile = traceable(readFileFunc, {
  name: "read_file",
});

async function writeFileFunc(
  sandbox: Sandbox,
  filePath: string,
  content: string,
  args?: {
    workDir?: string;
  },
): Promise<{
  success: boolean;
  output: string;
}> {
  try {
    const delimiter = "EOF_" + Date.now() + "_" + Math.random().toString(36);
    const writeCommand = `cat > "${filePath}" << '${delimiter}'
${content}
${delimiter}`;
    const writeOutput = await sandbox.commands.run(writeCommand, {
      cwd: args?.workDir,
    });
    // Add an extra 5 min timeout to the sandbox.
    await sandbox.setTimeout(TIMEOUT_MS);

    if (writeOutput.exitCode !== 0) {
      logger.error(`Error writing file '${filePath}' to sandbox via cat:`, {
        writeOutput,
      });
      return {
        success: false,
        output: `FAILED TO WRITE FILE to sandbox '${filePath}'. Exit code: ${writeOutput.exitCode}. Stderr: ${writeOutput.stderr}. Stdout: ${writeOutput.stdout}`,
      };
    }
    if (writeOutput.stderr) {
      logger.warn(
        `Stderr while writing file '${filePath}' to sandbox via cat: ${writeOutput.stderr}`,
      );
    }
    return {
      success: true,
      output: `Successfully wrote file '${filePath}' to sandbox via cat.`,
    };
  } catch (e: any) {
    logger.error(
      `Exception while trying to write file '${filePath}' to sandbox via cat:`,
      {
        ...(e instanceof Error
          ? { name: e.name, message: e.message, stack: e.stack }
          : { error: e }),
      },
    );

    let outputMessage = `FAILED TO EXECUTE WRITE COMMAND for sandbox '${filePath}'.`;
    const errorFields = getSandboxErrorFields(e);
    if (errorFields) {
      outputMessage += `\nExit code: ${errorFields.exitCode}.\nStderr: ${errorFields.stderr}.\nStdout: ${errorFields.stdout}`;
    } else {
      outputMessage += ` Error: ${(e as Error).message || String(e)}`;
    }

    return {
      success: false,
      output: outputMessage,
    };
  }
}

export const writeFile = traceable(writeFileFunc, {
  name: "write_file",
});
