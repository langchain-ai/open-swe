import "@langchain/langgraph/zod";
import { z } from "zod";
import { withLangGraph } from "@langchain/langgraph/zod";
import * as path from "path";
import { DeepAgentState } from "deepagents";
import { FILE_EDIT_COMMANDS } from "./constants.js";
import { Command, CommandArgs, ApprovalKey, FileEditCommandArgs, ExecuteBashCommandArgs, FileSystemCommandArgs, ApprovedOperations } from "./types.js";

export const CodingAgentState: any = DeepAgentState.extend({
  approved_operations: withLangGraph(z.custom<ApprovedOperations>().optional(), {
    reducer: {
      schema: z.custom<ApprovedOperations>().optional(),
      fn: (_state: ApprovedOperations | undefined, update: ApprovedOperations | undefined) => update,
    },
    default: () => ({ cached_approvals: new Set<string>() }),
  }),
});

export type CodingAgentStateType = z.infer<typeof CodingAgentState>;

/**
 * Helper functions for the coding agent state
 */
export class AgentStateHelpers {
  static getApprovalKey(command: Command, args: CommandArgs): ApprovalKey {
    let targetDir: string | null = null;

    if (FILE_EDIT_COMMANDS.has(command)) {
      const fileArgs = args as FileEditCommandArgs;
      const filePath = fileArgs.file_path || fileArgs.path;
      if (filePath) {
        targetDir = path.dirname(path.resolve(filePath));
      }
    } else if (command === "execute_bash") {
      const bashArgs = args as ExecuteBashCommandArgs;
      targetDir = bashArgs.cwd || process.cwd();
    } else if (["ls", "glob", "grep"].includes(command)) {
      const fsArgs = args as FileSystemCommandArgs;
      targetDir = fsArgs.path || fsArgs.directory || process.cwd();
    }

    if (!targetDir) {
      targetDir = process.cwd();
    }

    // Create a cache key: command_type:normalized_directory
    const normalizedDir = path.normalize(targetDir);
    return `${command}:${normalizedDir}`;
  }

  /**
   * Check if a command/directory combination has been previously approved.
   */
  static isOperationApproved(
    state: CodingAgentStateType,
    command: Command,
    args: CommandArgs,
  ): boolean {
    if (
      !state.approved_operations ||
      !state.approved_operations.cached_approvals
    ) {
      return false;
    }

    const approvalKey = this.getApprovalKey(command, args);
    return state.approved_operations.cached_approvals.has(approvalKey);
  }

  /**
   * Add a command/directory combination to the approved operations cache.
   */
  static addApprovedOperation(
    state: CodingAgentStateType,
    command: Command,
    args: CommandArgs,
  ): void {
    if (!state.approved_operations) {
      state.approved_operations = { cached_approvals: new Set<string>() };
    }

    if (!state.approved_operations.cached_approvals) {
      state.approved_operations.cached_approvals = new Set<string>();
    }

    const approvalKey = this.getApprovalKey(command, args);
    state.approved_operations.cached_approvals.add(approvalKey);
  }
}
