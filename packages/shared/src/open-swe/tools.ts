import { z } from "zod";
import { TargetRepository, GraphConfig } from "./types.js";
import { getRepoAbsolutePath } from "../git.js";
import { TIMEOUT_SEC } from "../constants.js";
import { isLocalMode, getLocalWorkingDirectory } from "./local-mode.js";

export function createApplyPatchToolFields(targetRepository: TargetRepository) {
  const repoRoot = getRepoAbsolutePath(targetRepository);
  const applyPatchToolSchema = z.object({
    diff: z
      .string()
      .describe(
        `The diff to apply. Use a standard diff format. Ensure this field is ALWAYS provided.`,
      ),
    file_path: z.string().describe("The file path to apply the diff to."),
  });

  return {
    name: "apply_patch",
    description:
      "Applies a diff to a file given a file path and diff content." +
      `The working directory this diff will be applied to is \`${repoRoot}\`. Ensure the file paths you provide are relative to this directory.`,
    schema: applyPatchToolSchema,
  };
}

export function createRequestHumanHelpToolFields() {
  const requestHumanHelpSchema = z.object({
    help_request: z
      .string()
      .describe(
        "The help request to send to the human. Should be concise, but descriptive.\n" +
          "IMPORTANT: This should be a request which the user can help with, such as providing context into where a function lives/is used within a codebase, or answering questions about how to run scripts.\n" +
          "IMPORTANT: The user does NOT have access to the filesystem you're running on, and thus can not make changes to the code for you.",
      ),
  });
  return {
    name: "request_human_help",
    schema: requestHumanHelpSchema,
    description:
      "Use this tool to request help from the human. This should only be called if you are stuck, and you are unable to continue. This will pause your execution until the user responds. You will not be able to go back and fourth with the user, so ensure the help request contains all of the necessary information and context the user might need to respond to your request.",
  };
}

export function createSessionPlanToolFields() {
  const sessionPlanSchema = z.object({
    title: z
      .string()
      .describe(
        "The title of the plan. Should be a short, one sentence description of the user's request/plan generated to fulfill it.",
      ),
    plan: z
      .array(z.string())
      .describe("The plan to address the user's request."),
  });
  return {
    name: "session_plan",
    description: "Call this tool when you are ready to generate a plan.",
    schema: sessionPlanSchema,
  };
}

export function createShellToolFields(targetRepository: TargetRepository) {
  const repoRoot = getRepoAbsolutePath(targetRepository);
  const shellToolSchema = z.object({
    command: z
      .array(z.string())
      .describe(
        "The command to run. Ensure the command is properly formatted, with arguments in the correct order, and including any wrapping strings, quotes, etc. By default, this command will be executed in the root of the repository, unless a custom workdir is specified.",
      ),
    workdir: z
      .string()
      .default(repoRoot)
      .describe(
        `The working directory for the command. Defaults to the root of the repository (${repoRoot}). You should only specify this if the command you're running can not be executed from the root of the repository.`,
      ),
    timeout: z
      .number()
      .optional()
      .default(TIMEOUT_SEC)
      .describe(
        "The maximum time to wait for the command to complete in seconds. For commands which may require a long time to complete, such as running tests, you should increase this value.",
      ),
  });
  return {
    name: "shell",
    description: "Runs a shell command, and returns its output.",
    schema: shellToolSchema,
  };
}

export function createUpdatePlanToolFields() {
  const updatePlanSchema = z.object({
    update_plan_reasoning: z
      .string()
      .describe(
        "The reasoning for why you are updating the plan. This should include context which will be useful when actually updating the plan, such as what plan items to update, edit, or remove, along with any other context that would be useful when updating the plan.",
      ),
  });

  return {
    name: "update_plan",
    schema: updatePlanSchema,
    description:
      "Call this tool to update the current plan. This should ONLY be called if you want to remove, edit, or add plan items to the current plan." +
      "\nDo NOT call this tool to mark a plan item as completed, or add a summary." +
      "\nYou can not edit/remove completed plan items. This tool can only be used to update/add/remove plan items from the remaining and current plan items." +
      "\nThe reasoning you pass to this tool will be used in the step that actually updates the plan, so ensure it is useful and concise.",
  };
}

export function createGrepToolFields(targetRepository: TargetRepository) {
  const repoRoot = getRepoAbsolutePath(targetRepository);
  const searchSchema = z.object({
    query: z
      .string()
      .describe(
        "The string or regex to search the codebase for. If passing a plain string, ensure to also set the 'match_string' field to true. If passing a regex, ensure to also set the 'match_string' field to false.",
      ),

    match_string: z
      .boolean()
      .optional()
      .default(false)
      .describe(
        "Whether or not to treat the query as a fixed string to search for. If true, it will search for results which match the query exactly. If false, the query will be treated as a regex. Defaults to false.",
      ),

    case_sensitive: z
      .boolean()
      .optional()
      .default(false)
      .describe(
        "Whether or not to make the search case sensitive. Defaults to false.",
      ),

    context_lines: z
      .number()
      .optional()
      .default(0)
      .describe("Number of lines of context to include before/after matches."),

    exclude_files: z
      .string()
      .optional()
      .describe("Glob pattern of files to exclude"),

    include_files: z
      .string()
      .optional()
      .describe("Glob pattern of files to include"),

    max_results: z
      .number()
      .optional()
      .default(0)
      .describe(
        "Maximum number of results to return. Defaults to 0, which returns all results.",
      ),
    file_types: z
      .array(z.string())
      .optional()
      .describe("Restrict to certain file extensions (e.g., ['.js', '.ts'])."),
    follow_symlinks: z
      .boolean()
      .optional()
      .default(false)
      .describe("Whether or not to follow symlinks. Defaults to false."),
  });

  return {
    name: "grep",
    schema: searchSchema,
    description: `Execute a grep (ripgrep) search in the repository. Should be used to search for content via string matching or regex in the codebase. The working directory this command will be executed in is \`${repoRoot}\`.`,
  };
}

// Only used for type inference
const _tmpSearchToolSchema = createGrepToolFields({
  owner: "x",
  repo: "x",
}).schema;
export type GrepCommand = z.infer<typeof _tmpSearchToolSchema>;

function escapeShellArg(arg: string): string {
  // If the string contains a single quote, close the string, escape the single quote, and reopen it
  // Example: foo'bar → 'foo'\''bar'
  return `'${arg.replace(/'/g, `'\\''`)}'`;
}

export function formatGrepCommand(
  cmd: GrepCommand,
  options?: {
    excludeRequiredFlags?: boolean;
  },
): string[] {
  const args = ["rg"];

  // Required flags to keep formatting and output consistent
  const requiredFlags = ["--color=never", "--line-number", "--heading"];

  if (!options?.excludeRequiredFlags) {
    args.push(...requiredFlags);
  }

  // Case sensitivity
  if (!cmd.case_sensitive) {
    args.push("-i");
  }

  // Regex vs fixed string
  if (cmd.match_string) {
    args.push("--fixed-strings");
  }

  // Context lines
  if (cmd.context_lines && cmd.context_lines > 0) {
    args.push(`-C`, String(cmd.context_lines));
  }

  // File globs - use ripgrep's glob handling instead of shell expansion
  if (cmd.include_files) {
    // Quote the glob pattern to prevent shell interpretation
    args.push("--glob", escapeShellArg(cmd.include_files));
  }

  if (cmd.exclude_files) {
    // Quote the exclude pattern to prevent shell interpretation
    args.push("--glob", escapeShellArg(`!${cmd.exclude_files}`));
  }

  // File types
  if (cmd.file_types && cmd.file_types.length > 0) {
    // Process each file type individually to avoid glob expansion issues
    for (const ext of cmd.file_types) {
      // Normalize extension format (ensure it has a leading dot)
      const normalizedExt = ext.startsWith(".") ? ext : `.${ext}`;
      // Quote the glob pattern to prevent shell interpretation
      args.push("--glob", escapeShellArg(`**/*${normalizedExt}`));
    }
  }

  // Follow symlinks
  if (cmd.follow_symlinks) {
    args.push("-L");
  }

  // Max results (0 = unlimited)
  if (cmd.max_results && cmd.max_results > 0) {
    args.push("--max-count", String(cmd.max_results));
  }

  // The query (must come after path for ripgrep to interpret it correctly)
  if (cmd.query) {
    // Double-quote the pattern to ensure it's treated as a pattern and not a path
    args.push(escapeShellArg(cmd.query));
  }

  return args;
}

/**
 * Format a shell command for display purposes
 */
export function formatShellCommand(
  command: string[],
  workdir?: string,
): string {
  const commandStr = command.join(" ");
  return workdir ? `${commandStr} (in ${workdir})` : commandStr;
}

/**
 * Format a view command for display purposes
 */
export function formatViewCommand(path: string): string {
  return `cat "${path}"`;
}

/**
 * Format a search documents command for display purposes
 */
export function formatSearchDocumentsCommand(
  query: string,
  url: string,
): string {
  return `search for "${query}" in ${url}`;
}

export function createMarkTaskNotCompletedToolFields() {
  const markTaskNotCompletedToolSchema = z.object({
    reasoning: z
      .string()
      .describe(
        "A concise reasoning summary for the status of the current task, explaining why you think it is not completed.",
      ),
  });

  const markTaskNotCompletedTool = {
    name: "mark_task_not_completed",
    description:
      "Mark the current task as not completed, along with a concise reasoning summary to support the status.",
    schema: markTaskNotCompletedToolSchema,
  };

  return markTaskNotCompletedTool;
}

export function createMarkTaskCompletedToolFields() {
  const markTaskCompletedToolSchema = z.object({
    completed_task_summary: z
      .string()
      .describe(
        "A detailed summary of the actions you took to complete the current task. " +
          "Include specifics into the actions you took, insights you learned about the codebase while completing the task, and any other context which would be useful to another developer reviewing the actions you took. " +
          "You may include file paths and lists of the changes you made, but do not include full file contents or full code changes. " +
          "Ensure your summary is concise, thoughtful and helpful.",
      ),
  });

  const markTaskCompletedTool = {
    name: "mark_task_completed",
    description:
      "Mark the current task as completed, and provide a concise reasoning summary on the actions you took to complete the task.",
    schema: markTaskCompletedToolSchema,
  };

  return markTaskCompletedTool;
}

export function createInstallDependenciesToolFields(
  targetRepository: TargetRepository,
) {
  const repoRoot = getRepoAbsolutePath(targetRepository);

  const installDependenciesToolSchema = z.object({
    command: z
      .array(z.string())
      .describe("The command to run to install dependencies."),
    workdir: z
      .string()
      .default(repoRoot)
      .describe(
        `The working directory to run the command in. The default working directory this command will be executed in is the root of the repository: \`${repoRoot}\`. If you want to execute this install command inside a different location, pass a path to this field.`,
      ),
  });

  return {
    name: "install_dependencies",
    description:
      "Installs dependencies for the repository. You should only call this tool if you need to install dependencies for a specific task. Ensure you only call this tool after gathering context on how to install dependencies, such as the package manager, proper install command, etc.",
    schema: installDependenciesToolSchema,
  };
}

export function createOpenPrToolFields() {
  const openPrToolSchema = z.object({
    title: z
      .string()
      .describe(
        "The title of the pull request. Ensure this is a concise and thoughtful title. You should follow conventional commit title format (e.g. 'fix:', 'feat:', 'chore:', etc.).",
      ),
    body: z
      .string()
      .optional()
      .describe(
        "The body of the pull request. This should provide a concise description what the PR changes. Do not over-explain, or add technical details unless they're the absolute minimum needed. The user should be able to quickly read your description, and understand what the PR does. Remember: if they want the technical details they can read the changed files, so you don't need to go into great detail here.",
      ),
  });

  return {
    name: "open_pr",
    schema: openPrToolSchema,
    description: "Use this tool to open a pull request.",
  };
}

export function createScratchpadFields(whenMessage: string) {
  const scratchpadSchema = z.object({
    scratchpad: z
      .array(z.string())
      .describe(
        `Write concise, technical, and useful notes to your scratchpad. These notes will be saved for you to use ${whenMessage}.`,
      ),
  });

  return {
    name: "scratchpad",
    schema: scratchpadSchema,
    description:
      `Use this tool to write & save technical notes on the actions you take, and observations you make, and any notes you believe will be useful ${whenMessage}.` +
      " This should be called if you come across context which you believe will be useful to you during later steps.",
  };
}

export function createDiagnoseErrorToolFields() {
  const diagnoseErrorToolSchema = z.object({
    diagnosis: z.string().describe("The diagnosis of the error."),
  });

  return {
    name: "diagnose_error",
    description: "Diagnoses an error given a diagnosis.",
    schema: diagnoseErrorToolSchema,
  };
}

export function createGetURLContentToolFields() {
  const getURLContentSchema = z.object({
    url: z
      .string()
      .describe(
        "The URL to get the content of. Returns the page content in markdown format.",
      ),
  });

  return {
    name: "get_url_content",
    description: "Get the full page content of a given URL in markdown format.",
    schema: getURLContentSchema,
  };
}

/**
 * Format a get URL content command for display purposes
 */
export function formatGetURLContentCommand(url: string): string {
  return `curl ${url}`;
}

/**
 * Format a str_replace_based_edit_tool command for display purposes
 */
export function formatStrReplaceEditCommand(
  command: string,
  path: string,
): string {
  switch (command) {
    case "view":
      return `view file ${path}`;
    case "str_replace":
      return `replace text in ${path}`;
    case "create":
      return `create file ${path}`;
    case "insert":
      return `insert text in ${path}`;
    default:
      return `${command} ${path}`;
  }
}

export function createSearchDocumentForToolFields() {
  const searchDocumentForSchema = z.object({
    url: z
      .string()
      .describe(
        "The URL of the document to search within. This should be a URL that was previously fetched and processed.",
      ),
    query: z
      .string()
      .describe(
        "The natural language query to search for within the document content. This query will be passed to an LLM which will use it to extract relevant content from the document. Be specific about what information you're looking for.",
      ),
  });

  return {
    name: "search_document_for",
    description:
      "Search for specific information within a previously fetched document using natural language queries. This tool is particularly useful when working with large documents that have been summarized with a table of contents. " +
      "This tool should only be called after a documentation or a web page has been read and summarized as a table of contents and you need to search for specific information within the document.",
    schema: searchDocumentForSchema,
  };
}

export function createWriteTechnicalNotesToolFields() {
  const writeTechnicalNotesSchema = z.object({
    notes: z
      .string()
      .describe(
        "The notes you've generated based on the conversation history.",
      ),
  });

  return {
    name: "write_technical_notes",
    description:
      "Write technical notes based on the conversation history provided. Ensure these notes are concise, but still containing enough information to be useful to you when you go to execute the plan.",
    schema: writeTechnicalNotesSchema,
  };
}

export function createConversationHistorySummaryToolFields() {
  const conversationHistorySummarySchema = z.object({
    reasoning: z.string(),
  });

  return {
    name: "summarize_conversation_history",
    description:
      "<not used as an actual tool call. only used as shared types between the client and agent>",
    schema: conversationHistorySummarySchema,
  };
}

export function createCodeReviewMarkTaskCompletedFields() {
  const markTaskCompletedSchema = z.object({
    review: z
      .string()
      .describe(
        "Your final review for the completed task. This should be concise, but descriptive.",
      ),
  });

  return {
    name: "code_review_mark_task_completed",
    schema: markTaskCompletedSchema,
    description:
      "Use this tool to mark a task as completed. This should be called if you determine that the task has been successfully completed.",
  };
}

export function createCodeReviewMarkTaskNotCompleteFields() {
  const markTaskNotCompleteSchema = z.object({
    review: z
      .string()
      .describe(
        "Your final review for the completed task. This should be concise, but descriptive.",
      ),
    additional_actions: z
      .array(z.string())
      .describe(
        "A list of additional actions to take which will successfully satisfy your review, and complete the task.",
      ),
  });

  return {
    name: "code_review_mark_task_not_complete",
    schema: markTaskNotCompleteSchema,
    description:
      "Use this tool to mark a task as not complete. This should be called if you determine that the task has not been successfully completed, and you have additional tasks the programmer should take to successfully complete the task.",
  };
}

export function createReviewStartedToolFields() {
  const reviewStartedSchema = z.object({
    review_started: z.boolean(),
  });

  return {
    name: "review_started",
    description:
      "<not used as an actual tool call. only used as shared types between the client and agent>",
    schema: reviewStartedSchema,
  };
}

export function createTextEditorToolFields(
  targetRepository: TargetRepository,
  config: GraphConfig,
) {
  const repoRoot = isLocalMode(config)
    ? getLocalWorkingDirectory()
    : getRepoAbsolutePath(targetRepository);
  const textEditorToolSchema = z.object({
    command: z
      .enum(["view", "str_replace", "create", "insert"])
      .describe("The command to execute: view, str_replace, create, or insert"),
    path: z
      .string()
      .describe("The path to the file or directory to operate on"),
    view_range: z
      .tuple([z.number(), z.number()])
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
      .describe(
        "The content to write to the new file. Required for create command.",
      ),
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
      `The working directory is \`${repoRoot}\`. Ensure file paths are absolute and properly formatted. ` +
      "Supports commands: view (read file/directory), str_replace (replace text), create (new file), insert (add text at line).",
    schema: textEditorToolSchema,
  };
}

export function createViewToolFields(
  targetRepository: TargetRepository,
  config?: GraphConfig,
) {
  const repoRoot =
    config && isLocalMode(config)
      ? getLocalWorkingDirectory()
      : getRepoAbsolutePath(targetRepository);
  const viewSchema = z.object({
    command: z.enum(["view"]).describe("The command to execute: view"),
    path: z
      .string()
      .describe("The path to the file or directory to operate on"),
    view_range: z
      .array(z.number())
      .optional()
      .describe(
        "Optional array of two integers [start, end] specifying line numbers to view. Line numbers are 1-indexed. Use -1 for end to read to end of file. Only applies to view command. If this is passed, ensure it is a valid array, containing only two positive integers.",
      ),
  });

  return {
    name: "view",
    description:
      "A text editor tool that can view files. " +
      `The working directory is \`${repoRoot}\`. Ensure file paths are absolute and properly formatted. ` +
      "Supports commands: view (read file/directory).",
    schema: viewSchema,
  };
}

export function createWriteDefaultTsConfigToolFields(
  targetRepository: TargetRepository,
) {
  const repoRoot = getRepoAbsolutePath(targetRepository);

  const writeDefaultTsConfigToolSchema = z.object({
    workdir: z
      .string()
      .default(repoRoot)
      .describe(
        `The directory which the tsconfig.json file will be written to. The default value is the root of the repository: \`${repoRoot}\`.`,
      ),
  });

  return {
    name: "write_default_tsconfig",
    description:
      "Writes a default tsconfig.json file to the specified directory. This should ONLY be called when creating a new TypeScript project.",
    schema: writeDefaultTsConfigToolSchema,
  };
}
