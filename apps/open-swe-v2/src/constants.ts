/**
 * Constants for Open SWE V2 coding agent.
 */

/**
 * File operation commands that require approval in the approval system
 */
export const FILE_EDIT_COMMANDS = new Set([
  "write_file",
  "str_replace_based_edit_tool",
  "edit_file",
]);

/**
 * All commands that require approval (includes file operations plus other system operations)
 */
export const WRITE_COMMANDS = new Set([
  "write_file",
  "execute_bash",
  "str_replace_based_edit_tool",
  "ls",
  "edit_file",
  "glob",
  "grep",
]);
