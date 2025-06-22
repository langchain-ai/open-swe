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
  // Main ripgrep command schema
  const ripgrepCommandSchema = z.object({
    pattern: z
      .string()
      .optional()
      .describe(
        "The search pattern (regex). Leave empty when using flags like --files or --type-list",
      ),

    paths: z
      .array(z.string())
      .optional()
      .describe(
        "Files or directories to search. If empty, searches current directory",
      ),

    flags: z
      .array(z.string())
      .optional()
      .describe(
        'Array of flags with their values. Examples: ["-i", "--type=rust", "-A", "3", "--files"]. Short flags like -i can be standalone, flags with values can be separate strings or use = for long flags',
      ),
  });

  return {
    name: "rg",
    schema: ripgrepCommandSchema,
    description: `Call this tool to run the rg command (ripgrep). This should ONLY be called if you want to search for files in the repository. The working directory this command will be executed in is \`${repoRoot}\`.`,
  };
}

// Only used for type inference
const _tmpRgToolSchema = createRgToolFields({ owner: "x", repo: "x" }).schema;
export type RipgrepCommand = z.infer<typeof _tmpRgToolSchema>;

export function formatRgCommand(cmd: RipgrepCommand): string[] {
  const args = ["rg"];

  if (cmd.flags) {
    args.push(...cmd.flags);
  }

  if (cmd.pattern) {
    args.push(cmd.pattern);
  }

  if (cmd.paths) {
    args.push(...cmd.paths);
  }

  return args;
}

export function createSetTaskStatusToolFields() {
  const setTaskStatusToolSchema = z.object({
    reasoning: z
      .string()
      .describe(
        "A concise reasoning summary for the status of the current task, explaining why you think it is completed or not completed.",
      ),
    task_status: z
      .enum(["completed", "not_completed"])
      .describe(
        "The status of the current task, based on the reasoning provided.",
      ),
  });

  const setTaskStatusTool = {
    name: "set_task_status",
    description:
      "The status of the current task, along with a concise reasoning summary to support the status.",
    schema: setTaskStatusToolSchema,
  };

  return setTaskStatusTool;
}

export function createInstallDependenciesToolFields(
  targetRepository: TargetRepository,
) {
  const repoRoot = getRepoAbsolutePath(targetRepository);

  const installDependenciesToolSchema = z.object({
    command: z.string().describe("The command to run to install dependencies."),
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
