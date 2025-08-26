import { z } from "zod";

/**
 * Type definitions for Open SWE V2 coding agent
 */

// Command argument types
export interface FileEditCommandArgs {
  file_path?: string;
  path?: string;
  [key: string]: any;
}

export interface ExecuteBashCommandArgs {
  cwd?: string;
  [key: string]: any;
}

export interface FileSystemCommandArgs {
  path?: string;
  directory?: string;
  [key: string]: any;
}

export interface GenericCommandArgs {
  [key: string]: any;
}

// Union type for all possible command arguments
export type CommandArgs =
  | FileEditCommandArgs
  | ExecuteBashCommandArgs
  | FileSystemCommandArgs
  | GenericCommandArgs;

// Command types
export type FileEditCommand =
  | "write_file"
  | "str_replace_based_edit_tool"
  | "edit_file";
export type ExecuteBashCommand = "execute_bash";
export type FileSystemCommand = "ls" | "glob" | "grep";
export type GenericCommand = string;

export type Command =
  | FileEditCommand
  | ExecuteBashCommand
  | FileSystemCommand
  | GenericCommand;

// Approval key type
export type ApprovalKey = string;

// Approved operations schema
export const ApprovedOperationsSchema = z
  .object({
    cached_approvals: z.set(z.string()).default(() => new Set<string>()),
  })
  .optional();

export type ApprovedOperations = z.infer<typeof ApprovedOperationsSchema>;

// Note: AgentStateHelpers class uses static methods, so no interface implementation is needed
// The method signatures are:
// - static getApprovalKey(command: Command, args: CommandArgs): ApprovalKey
// - static isOperationApproved(state: CodingAgentStateType, command: Command, args: CommandArgs): boolean
// - static addApprovedOperation(state: CodingAgentStateType, command: Command, args: CommandArgs): void

// Type for the approval key generation result
export interface ApprovalKeyResult {
  command: Command;
  targetDir: string;
  normalizedDir: string;
  approvalKey: ApprovalKey;
}
