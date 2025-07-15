import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "./logger.js";
import { getSandboxErrorFields } from "./sandbox-error-fields.js";
import { traceable } from "langsmith/traceable";

const logger = createLogger(LogLevel.INFO, "ReadWriteUtil");

async function handleCreateFile(
  sandbox: Sandbox,
  filePath: string,
  args?: {
    workDir?: string;
  },
) {
  try {
    const touchCommand = `touch "${filePath}"`;
    const touchOutput = await sandbox.process.executeCommand(
      touchCommand,
      args?.workDir,
    );
    return touchOutput;
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    if (errorFields) {
      return errorFields;
    }
    return {
      exitCode: 1,
      error: e instanceof Error ? e.message : String(e),
      stdout: "",
      stderr: "",
    };
  }
}

async function readFileFunc(inputs: {
  sandbox: Sandbox;
  filePath: string;
  workDir?: string;
}): Promise<{
  success: boolean;
  output: string;
}> {
  const { sandbox, filePath, workDir } = inputs;
  try {
    const readOutput = await sandbox.process.executeCommand(
      `cat "${filePath}"`,
      workDir,
    );

    if (readOutput.exitCode !== 0) {
      const errorResult = readOutput.result ?? readOutput.artifacts?.stdout;
      return {
        success: false,
        output: `FAILED TO READ FILE from sandbox '${filePath}'. Exit code: ${readOutput.exitCode}.\nResult: ${errorResult}`,
      };
    }

    return {
      success: true,
      output: readOutput.result,
    };
  } catch (e: any) {
    if (e instanceof Error && e.message.includes("No such file or directory")) {
      const createOutput = await handleCreateFile(sandbox, filePath, {
        workDir,
      });
      if (createOutput.exitCode !== 0) {
        return {
          success: false,
          output: `FAILED TO EXECUTE READ COMMAND for sandbox '${filePath}'. Error: ${(e as Error).message || String(e)}`,
        };
      } else {
        // If the file was created successfully, try reading it again.
        return readFile(inputs);
      }
    }

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
      const errorResult = errorFields.result ?? errorFields.artifacts?.stdout;

      outputMessage += `\nExit code: ${errorFields.exitCode}\nResult: ${errorResult}`;
    } else {
      outputMessage += ` Error: ${(e as Error).message || String(e)}`;
    }

    if (outputMessage.includes("No such file or directory")) {
      outputMessage += `\nPlease check the file paths you passed to \`workdir\` and \`file_path\` to ensure they are valid, and when combined they point to a valid file in the sandbox.`;
    }

    return {
      success: false,
      output: outputMessage,
    };
  }
}

export const readFile = traceable(readFileFunc, {
  name: "read_file",
  processInputs: (inputs) => {
    const { sandbox: _sandbox, ...rest } = inputs;
    return rest;
  },
});

async function writeFileFunc(inputs: {
  sandbox: Sandbox;
  filePath: string;
  content: string;
  workDir?: string;
}): Promise<{
  success: boolean;
  output: string;
}> {
  const { sandbox, filePath, content, workDir } = inputs;
  try {
    const delimiter = "EOF_" + Date.now() + "_" + Math.random().toString(36);
    const writeCommand = `cat > "${filePath}" << '${delimiter}'
${content}
${delimiter}`;
    const writeOutput = await sandbox.process.executeCommand(
      writeCommand,
      workDir,
    );

    if (writeOutput.exitCode !== 0) {
      const errorResult = writeOutput.result ?? writeOutput.artifacts?.stdout;
      return {
        success: false,
        output: `FAILED TO WRITE FILE to sandbox '${filePath}'. Exit code: ${writeOutput.exitCode}\nResult: ${errorResult}`,
      };
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
      const errorResult = errorFields.result ?? errorFields.artifacts?.stdout;
      outputMessage += `\nExit code: ${errorFields.exitCode}\nResult: ${errorResult}`;
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
  processInputs: (inputs) => {
    const { sandbox: _sandbox, ...rest } = inputs;
    return rest;
  },
});
