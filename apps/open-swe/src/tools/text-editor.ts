import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { readFile, writeFile } from "../utils/read-write.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";

const logger = createLogger(LogLevel.INFO, "TextEditorTool");

function createTextEditorToolFields(targetRepository: any) {
  const repoRoot = getRepoAbsolutePath(targetRepository);
  const textEditorToolSchema = z.object({
    command: z
      .enum(["view", "str_replace", "create", "insert"])
      .describe("The command to execute: view, str_replace, create, or insert"),
    path: z
      .string()
      .describe("The path to the file or directory to operate on"),
    view_range: z
      .array(z.number())
      .length(2)
      .optional()
      .describe(
        "Optional array of two integers [start, end] specifying line numbers to view. Line numbers are 1-indexed. Use -1 for end to read to end of file. Only applies to view command.",
      ),
    old_str: z
      .string()
      .optional()
      .describe(
        "The text to replace (must match exactly, including whitespace and indentation). Required for str_replace command.",
      ),
    new_str: z
      .string()
      .optional()
      .describe(
        "The new text to insert. Required for str_replace and insert commands.",
      ),
    file_text: z
      .string()
      .optional()
      .describe("The content to write to the new file. Required for create command."),
    insert_line: z
      .number()
      .optional()
      .describe(
        "The line number after which to insert the text (0 for beginning of file). Required for insert command.",
      ),
  });

  return {
    name: "str_replace_based_edit_tool",
    description:
      "A text editor tool that can view, create, and edit files. " +
      `The working directory is \`${repoRoot}\`. Ensure file paths are relative to this directory. ` +
      "Supports commands: view (read file/directory), str_replace (replace text), create (new file), insert (add text at line).",
    schema: textEditorToolSchema,
  };
}

async function handleViewCommand(
  sandbox: any,
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
      const lines = output.split('\n');
      const [start, end] = viewRange;
      const startIndex = Math.max(0, start - 1); // Convert to 0-indexed
      const endIndex = end === -1 ? lines.length : Math.min(lines.length, end);
      
      const selectedLines = lines.slice(startIndex, endIndex);
      const numberedLines = selectedLines.map(
        (line, index) => `${startIndex + index + 1}: ${line}`,
      );
      
      return numberedLines.join('\n');
    }

    // Return full file with line numbers
    const lines = output.split('\n');
    const numberedLines = lines.map((line, index) => `${index + 1}: ${line}`);
    return numberedLines.join('\n');
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    if (errorFields) {
      throw new Error(`Failed to view ${path}: ${errorFields.result || errorFields.error}`);
    }
    throw new Error(`Failed to view ${path}: ${e instanceof Error ? e.message : String(e)}`);
  }
}

async function handleStrReplaceCommand(
  sandbox: any,
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
  const occurrences = (fileContent.match(new RegExp(oldStr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []).length;
  
  if (occurrences === 0) {
    throw new Error(`No match found for replacement text in ${path}. Please check your text and try again.`);
  }
  
  if (occurrences > 1) {
    throw new Error(`Found ${occurrences} matches for replacement text in ${path}. Please provide more context to make a unique match.`);
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

async function handleCreateCommand(
  sandbox: any,
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
    throw new Error(`File ${path} already exists. Use str_replace to modify existing files.`);
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

async function handleInsertCommand(
  sandbox: any,
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

  const lines = fileContent.split('\n');
  
  // Insert at specified line (0 = beginning, 1 = after first line, etc.)
  const insertIndex = Math.max(0, Math.min(lines.length, insertLine));
  lines.splice(insertIndex, 0, newStr);
  
  const newContent = lines.join('\n');

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

export function createTextEditorTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
) {
  const textEditorTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const sandbox = await getSandboxSessionOrThrow(input);
        const workDir = getRepoAbsolutePath(state.targetRepository);
        
        const { command, path, view_range, old_str, new_str, file_text, insert_line } = input;

        let result: string;

        switch (command) {
          case "view":
            result = await handleViewCommand(sandbox, path, workDir, view_range);
            break;
          case "str_replace":
            if (!old_str || new_str === undefined) {
              throw new Error("str_replace command requires both old_str and new_str parameters");
            }
            result = await handleStrReplaceCommand(sandbox, path, workDir, old_str, new_str);
            break;
          case "create":
            if (!file_text) {
