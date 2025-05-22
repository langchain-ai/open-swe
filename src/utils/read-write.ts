import { Sandbox } from "@e2b/code-interpreter";

export async function readFile(
  sandbox: Sandbox,
  filePath: string,
): Promise<{
  success: boolean;
  output: string;
}> {
  try {
    const readOutput = await sandbox.commands.run(`cat "${filePath}"`);
    if (readOutput.exitCode !== 0) {
      console.error(
        `Error reading file '${filePath}' from sandbox via cat:`,
        readOutput,
      );
      return {
        success: false,
        output: `FAILED TO READ FILE from sandbox '${filePath}'. Exit code: ${readOutput.exitCode}.\nStderr: ${readOutput.stderr}.\nStdout: ${readOutput.stdout}`,
      };
    }
    if (readOutput.stderr) {
      console.warn(
        `Stderr while reading file '${filePath}' from sandbox via cat: ${readOutput.stderr}`,
      );
    }
    return {
      success: true,
      output: readOutput.stdout,
    };
  } catch (e: any) {
    console.error(
      `Exception while trying to read file '${filePath}' from sandbox via cat:`,
      e,
    );
    return {
      success: false,
      output: `FAILED TO EXECUTE READ COMMAND for sandbox '${filePath}'. Error: ${(e as Error).message || String(e)}`,
    };
  }
}

export async function writeFile(
  sandbox: Sandbox,
  filePath: string,
  content: string,
): Promise<{
  success: boolean;
  output: string;
}> {
  try {
    const writeCommand = `printf '%s' '${content}' > "${filePath}"`;
    const writeOutput = await sandbox.commands.run(writeCommand);

    if (writeOutput.exitCode !== 0) {
      console.error(
        `Error writing file '${filePath}' to sandbox via printf:`,
        writeOutput,
      );
      return {
        success: false,
        output: `FAILED TO WRITE FILE to sandbox '${filePath}'. Exit code: ${writeOutput.exitCode}. Stderr: ${writeOutput.stderr}. Stdout: ${writeOutput.stdout}`,
      };
    }
    if (writeOutput.stderr) {
      console.warn(
        `Stderr while writing file '${filePath}' to sandbox via printf: ${writeOutput.stderr}`,
      );
    }
    return {
      success: true,
      output: `Successfully wrote file '${filePath}' to sandbox via printf.`,
    };
  } catch (e: any) {
    console.error(
      `Exception while trying to write file '${filePath}' to sandbox via printf:`,
      e,
    );
    return {
      success: false,
      output: `FAILED TO EXECUTE WRITE COMMAND for sandbox '${filePath}'. Error: ${(e as Error).message || String(e)}`,
    };
  }
}
