import { Sandbox } from "@daytonaio/sdk";
import { readFile, writeFile } from "../../utils/read-write.js";
import { getSandboxErrorFields } from "../../utils/sandbox-error-fields.js";

export async function handleViewCommand(
  sandbox: Sandbox,
  path: string,
  workDir: string,
  viewRange?: [number, number],
): Promise<string> {
  try {
    // Check if path is a directory
    const statOutput = await sandbox.process.executeCommand(
      `stat -c %F "${path}"`,
      workDir,
    );

    if (statOutput.exitCode === 0 && statOutput.result?.includes("directory")) {
      // List directory contents
      const lsOutput = await sandbox.process.executeCommand(
        `ls -la "${path}"`,
        workDir,
      );

      if (lsOutput.exitCode !== 0) {
        throw new Error(`Failed to list directory: ${lsOutput.result}`);
      }

      return `Directory listing for ${path}:\n${lsOutput.result}`;
    }

    // Read file contents
    const { success, output } = await readFile({
      sandbox,
      filePath: path,
      workDir,
    });

    if (!success) {
      throw new Error(output);
    }

    // Apply view range if specified
    if (viewRange) {
      const lines = output.split("\n");
      const [start, end] = viewRange;
      const startIndex = Math.max(0, start - 1); // Convert to 0-indexed
      const endIndex = end === -1 ? lines.length : Math.min(lines.length, end);

      const selectedLines = lines.slice(startIndex, endIndex);
      const numberedLines = selectedLines.map(
        (line, index) => `${startIndex + index + 1}: ${line}`,
      );

      return numberedLines.join("\n");
    }

    // Return full file with line numbers
    const lines = output.split("\n");
    const numberedLines = lines.map((line, index) => `${index + 1}: ${line}`);
    return numberedLines.join("\n");
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    if (errorFields) {
      throw new Error(`Failed to view ${path}: ${errorFields.result}`);
    }
    throw new Error(
      `Failed to view ${path}: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
}

export async function handleStrReplaceCommand(
  sandbox: Sandbox,
  path: string,
  workDir: string,
  oldStr: string,
  newStr: string,
): Promise<string> {
  const { success: readSuccess, output: fileContent } = await readFile({
    sandbox,
    filePath: path,
    workDir,
  });

  if (!readSuccess) {
    throw new Error(`Failed to read file ${path}: ${fileContent}`);
  }

  // Count occurrences of old string
  const occurrences = (
    fileContent.match(
      new RegExp(oldStr.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g"),
    ) || []
  ).length;

  if (occurrences === 0) {
    throw new Error(
      `No match found for replacement text in ${path}. Please check your text and try again.`,
    );
  }

  if (occurrences > 1) {
    throw new Error(
      `Found ${occurrences} matches for replacement text in ${path}. Please provide more context to make a unique match.`,
    );
  }

  // Perform replacement
  const newContent = fileContent.replace(oldStr, newStr);

  const { success: writeSuccess, output: writeOutput } = await writeFile({
    sandbox,
    filePath: path,
    content: newContent,
    workDir,
  });

  if (!writeSuccess) {
    throw new Error(`Failed to write file ${path}: ${writeOutput}`);
  }

  return `Successfully replaced text in ${path} at exactly one location.`;
}

export async function handleCreateCommand(
  sandbox: Sandbox,
  path: string,
  workDir: string,
  fileText: string,
): Promise<string> {
  // Check if file already exists
  const { success: readSuccess } = await readFile({
    sandbox,
    filePath: path,
    workDir,
  });

  if (readSuccess) {
    throw new Error(
      `File ${path} already exists. Use str_replace to modify existing files.`,
    );
  }

  const { success: writeSuccess, output: writeOutput } = await writeFile({
    sandbox,
    filePath: path,
    content: fileText,
    workDir,
  });

  if (!writeSuccess) {
    throw new Error(`Failed to create file ${path}: ${writeOutput}`);
  }

  return `Successfully created file ${path}.`;
}

export async function handleInsertCommand(
  sandbox: Sandbox,
  path: string,
  workDir: string,
  insertLine: number,
  newStr: string,
): Promise<string> {
  const { success: readSuccess, output: fileContent } = await readFile({
    sandbox,
    filePath: path,
    workDir,
  });

  if (!readSuccess) {
    throw new Error(`Failed to read file ${path}: ${fileContent}`);
  }

  const lines = fileContent.split("\n");

  // Insert at specified line (0 = beginning, 1 = after first line, etc.)
  const insertIndex = Math.max(0, Math.min(lines.length, insertLine));
  lines.splice(insertIndex, 0, newStr);

  const newContent = lines.join("\n");

  const { success: writeSuccess, output: writeOutput } = await writeFile({
    sandbox,
    filePath: path,
    content: newContent,
    workDir,
  });

  if (!writeSuccess) {
    throw new Error(`Failed to write file ${path}: ${writeOutput}`);
  }

  return `Successfully inserted text in ${path} at line ${insertLine}.`;
}
