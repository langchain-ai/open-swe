import { z } from "zod";
import { TargetRepository } from "./types.js";
import { getRepoAbsolutePath } from "../git.js";
import { TIMEOUT_SEC } from "../constants.js";

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
        "The help request to send to the human. Should be concise, but descriptive.",
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
        "The maximum time to wait for the command to complete in seconds.",
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

export function createRgToolFields(targetRepository: TargetRepository) {
  const repoRoot = getRepoAbsolutePath(targetRepository);
  const ripgrepFlagSchema = z.object({
    flag: z
      .string()
      .describe(
        'The flag name without dashes. For short flags use single letter (e.g., "e" for -e), for long flags use the full name (e.g., "regexp" for --regexp). Some flags accept values, which should be provided in the value field.',
      ),
    value: z
      .string()
      .optional()
      .describe(
        'The value for flags that accept arguments. For example, with flag "type", value might be "rust". For flags that accept multiple values (like -e), create multiple flag objects.',
      ),
  });

  // Main ripgrep command schema
  const ripgrepCommandSchema = z.object({
    pattern: z
      .string()
      .optional()
      .describe(
        "The regular expression pattern to search for. This is the main search term. Can be omitted when using --files or --type-list mode. For patterns starting with dash, use the flags array with -e instead. Supports full regex syntax based on the selected engine.",
      ),

    paths: z
      .array(z.string())
      .optional()
      .describe(
        "Array of file paths or directories to search in. If omitted, searches recursively in the current directory. Directories are searched recursively by default. Can include both files and directories. Paths override any ignore rules.",
      ),

    flags: z
      .array(ripgrepFlagSchema)
      .optional()
      .describe(
        'Array of command-line flags and their values. Each flag is an object with "flag" (the flag name) and optional "value" (for flags that take arguments). Examples: {flag: "type", value: "rust"} for --type=rust, {flag: "i"} for -i (case insensitive), {flag: "A", value: "3"} for -A 3 (3 lines after context).',
      ),

    useStdin: z
      .boolean()
      .optional()
      .default(false)
      .describe(
        "If true, indicates that ripgrep should read from stdin instead of searching files. In this case, paths should be omitted. The pattern is still required unless using specific flags like --files.",
      ),

    mode: z
      .enum(["search", "files", "type-list"])
      .optional()
      .default("search")
      .describe(
        'The operation mode. "search" is the default pattern search. "files" lists files that would be searched (--files flag). "type-list" shows all file types (--type-list flag). This helps determine which arguments are required.',
      ),
  });

  return {
    name: "rg",
    schema: ripgrepCommandSchema,
    description: `Call this tool to run the rg command (ripgrep). This should ONLY be called if you want to search for files in the repository. The working directory this command will be executed in is \`${repoRoot}\`.`,
  };
}

const tmpRgToolSchema = createRgToolFields({ owner: "x", repo: "x" }).schema;
export type RipgrepCommand = z.infer<typeof tmpRgToolSchema>;

export function formatRgCommand(command: RipgrepCommand): string[] {
  const args: string[] = ["rg"];

  // Add flags first
  if (command.flags) {
    for (const { flag, value } of command.flags) {
      if (flag.length === 1) {
        // Short flag
        args.push(`-${flag}`);
        if (value !== undefined) {
          args.push(value);
        }
      } else {
        // Long flag
        if (value !== undefined) {
          args.push(`--${flag}=${value}`);
        } else {
          args.push(`--${flag}`);
        }
      }
    }
  }

  // Add mode-specific flags if not already included
  if (
    command.mode === "files" &&
    !command.flags?.some((f) => f.flag === "files")
  ) {
    args.push("--files");
  } else if (
    command.mode === "type-list" &&
    !command.flags?.some((f) => f.flag === "type-list")
  ) {
    args.push("--type-list");
  }

  // Add pattern if in search mode and pattern is provided
  if (
    command.mode === "search" &&
    command.pattern &&
    !command.flags?.some(
      (f) =>
        f.flag === "e" ||
        f.flag === "regexp" ||
        f.flag === "f" ||
        f.flag === "file",
    )
  ) {
    args.push(command.pattern);
  }

  // Add paths
  if (command.paths && command.paths.length > 0 && !command.useStdin) {
    args.push(...command.paths);
  }

  return args;
}
