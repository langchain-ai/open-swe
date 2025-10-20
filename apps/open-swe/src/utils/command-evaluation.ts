import { createLogger, LogLevel } from "./logger.js";
import { createCommandSafetyEvaluator } from "../tools/command-safety-evaluator.js";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import {
  formatGrepCommand,
  formatShellCommand,
  formatViewCommand,
  formatSearchDocumentsCommand,
  formatGetURLContentCommand,
  formatStrReplaceEditCommand,
  GrepCommand,
  createShellToolFields,
  createViewToolFields,
  createSearchDocumentForToolFields,
  createGetURLContentToolFields,
  createTextEditorToolFields,
} from "@openswe/shared/open-swe/tools";
import { ToolCall } from "@langchain/core/messages/tool";
import { z } from "zod";

const logger = createLogger(LogLevel.INFO, "CommandEvaluation");

// Type definitions for tool call arguments - derived from actual tool schemas. Underscores so the linter doesn't complain.
const dummyRepo = { owner: "dummy", repo: "dummy" };
const _shellTool = createShellToolFields(dummyRepo);
type ShellToolArgs = z.infer<typeof _shellTool.schema>;

const _viewTool = createViewToolFields(dummyRepo);
type ViewToolArgs = z.infer<typeof _viewTool.schema>;

const _searchDocumentsTool = createSearchDocumentForToolFields();
type SearchDocumentsToolArgs = z.infer<typeof _searchDocumentsTool.schema>;

const _getURLContentTool = createGetURLContentToolFields();
type GetURLContentToolArgs = z.infer<typeof _getURLContentTool.schema>;

const _textEditorTool = createTextEditorToolFields(dummyRepo, {});
type StrReplaceEditToolArgs = z.infer<typeof _textEditorTool.schema>;

export interface CommandEvaluation {
  toolCall: ToolCall;
  commandDescription: string;
  commandString: string;
  isSafe: boolean;
  reasoning: string;
  riskLevel: "low" | "medium" | "high";
}

export interface CommandEvaluationResult {
  safeCommands: CommandEvaluation[];
  unsafeCommands: CommandEvaluation[];
  allCommands: CommandEvaluation[];
  filteredToolCalls: ToolCall[];
  wasFiltered: boolean;
}

// Commands that are known to be safe for reading
const SAFE_READ_COMMANDS = [
  "ls",
  "cat",
  "head",
  "tail",
  "less",
  "more",
  "grep",
  "find",
  "locate",
  "file",
  "stat",
  "du",
  "df",
  "ps",
  "top",
  "htop",
  "free",
  "uptime",
  "who",
  "w",
  "id",
  "pwd",
  "echo",
  "printenv",
  "env",
  "which",
  "whereis",
  "man",
  "help",
  "info",
  "type",
  "hash",
  "history",
  "alias",
];

export function isSafeReadCommand(command: string): boolean {
  const lowerCommand = command.toLowerCase();

  // Check for known safe read commands
  for (const safeCmd of SAFE_READ_COMMANDS) {
    if (lowerCommand.startsWith(safeCmd.toLowerCase())) {
      return true;
    }
  }

  return false;
}

export function getCommandString(toolCall: ToolCall): {
  commandString: string;
  commandDescription: string;
} {
  let commandString = "";
  let commandDescription = "";

  if (toolCall.name === "shell") {
    const args = toolCall.args as ShellToolArgs;
    commandString = formatShellCommand(args.command, args.workdir);
    commandDescription = `${toolCall.name} - ${commandString}`;
  } else if (toolCall.name === "grep") {
    const args = toolCall.args as GrepCommand;
    const grepCommand = formatGrepCommand(args);
    commandString = grepCommand.join(" ");
    commandDescription = `${toolCall.name} - searching for "${args.query}"`;
  } else if (toolCall.name === "view") {
    const args = toolCall.args as ViewToolArgs;
    commandString = formatViewCommand(args.path);
    commandDescription = `${toolCall.name} - viewing ${args.path}`;
  } else if (toolCall.name === "search_documents_for") {
    const args = toolCall.args as SearchDocumentsToolArgs;
    commandString = formatSearchDocumentsCommand(args.query, args.url);
    commandDescription = `${toolCall.name} - searching documents for "${args.query}" in ${args.url}`;
  } else if (toolCall.name === "get_url_content") {
    const args = toolCall.args as GetURLContentToolArgs;
    commandString = formatGetURLContentCommand(args.url);
    commandDescription = `${toolCall.name} - fetching content from ${args.url}`;
  } else if (toolCall.name === "str_replace_based_edit_tool") {
    const args = toolCall.args as StrReplaceEditToolArgs;
    commandString = formatStrReplaceEditCommand(args.command, args.path);
    commandDescription = `${toolCall.name} - ${commandString}`;
  }

  return { commandString, commandDescription };
}

export async function evaluateCommands(
  commandToolCalls: ToolCall[],
  config: GraphConfig,
): Promise<CommandEvaluationResult> {
  const commandExecutingTools = [
    "shell",
    "grep",
    "view",
    "search_documents_for",
    "get_url_content",
    "str_replace_based_edit_tool",
  ];
  logger.info("Evaluating safety of command-executing tools", {
    commandToolCalls: commandToolCalls.map((c) => c.name),
  });

  // Create safety evaluator
  const safetyEvaluator = createCommandSafetyEvaluator(config);

  // Evaluate safety for each command
  const safetyEvaluations = await Promise.all(
    commandToolCalls.map(async (toolCall) => {
      const { commandString, commandDescription } = getCommandString(toolCall);

      try {
        const evaluation = await safetyEvaluator.invoke({
          command: commandString,
          tool_name: toolCall.name,
          args: toolCall.args,
        });

        const result = evaluation.result;
        return {
          toolCall,
          commandDescription,
          commandString,
          isSafe: result.is_safe,
          reasoning: result.reasoning,
          riskLevel: result.risk_level,
        };
      } catch (e) {
        logger.error("Failed to evaluate safety for command", {
          toolCall,
          error: e instanceof Error ? e.message : e,
        });
        // Default to unsafe if evaluation fails
        return {
          toolCall,
          commandDescription,
          commandString,
          isSafe: false,
          reasoning: "Failed to evaluate safety - defaulting to unsafe",
          riskLevel: "high" as const,
        };
      }
    }),
  );

  // Categorize commands
  const safeCommands = safetyEvaluations.filter(
    (evaluation) => evaluation.isSafe,
  );
  const unsafeCommands = safetyEvaluations.filter(
    (evaluation) => !evaluation.isSafe,
  );

  // Filter out only unsafe commands (allow safe write commands)
  const safeToolCalls = safeCommands.map((evaluation) => evaluation.toolCall);
  const otherToolCalls = commandToolCalls.filter(
    (toolCall) => !commandExecutingTools.includes(toolCall.name),
  );

  const filteredToolCalls = [...safeToolCalls, ...otherToolCalls];
  const wasFiltered = filteredToolCalls.length !== commandToolCalls.length;

  return {
    safeCommands,
    unsafeCommands,
    allCommands: safetyEvaluations,
    filteredToolCalls,
    wasFiltered,
  };
}

function isKnownSafeCommand(toolCall: ToolCall): boolean {
  if (toolCall.name !== "shell") {
    return false;
  }

  const args = toolCall.args as ShellToolArgs;
  if (!Array.isArray(args?.command) || args.command.length === 0) {
    return false;
  }

  const commandString = formatShellCommand(args.command, args.workdir);

  if (isSafeReadCommand(commandString)) {
    return true;
  }

  const sudoStrippedCommand = commandString.startsWith("sudo ")
    ? commandString.slice(5)
    : commandString;
  if (isSafeReadCommand(sudoStrippedCommand)) {
    return true;
  }

  const normalizedArgs = args.command.map((arg) => arg.toLowerCase());

  const sudoOffset = normalizedArgs[0] === "sudo" ? 1 : 0;
  const command = normalizedArgs[sudoOffset];

  if (!command) {
    return false;
  }

  if (command === "git") {
    const gitArgs = normalizedArgs.slice(sudoOffset + 1);
    if (gitArgs.some((arg) => arg === "status")) {
      return true;
    }
  }

  const originalCommand = args.command[sudoOffset];
  if (
    typeof originalCommand === "string" &&
    originalCommand.startsWith("./") &&
    originalCommand.toLowerCase().endsWith(".py")
  ) {
    return true;
  }

  if (command === "sed") {
    const sedArgs = normalizedArgs.slice(sudoOffset + 1);
    const hasInPlaceFlag = sedArgs.some(
      (arg) =>
        arg === "-i" ||
        arg.startsWith("-i") ||
        arg === "--in-place" ||
        arg.startsWith("--in-place"),
    );

    if (!hasInPlaceFlag) {
      return true;
    }
  }

  if (command !== "chmod") {
    return false;
  }

  const remainingArgs = normalizedArgs.slice(sudoOffset + 1);
  if (remainingArgs.length === 0) {
    return false;
  }

  const nonFlagArgs = remainingArgs.filter((arg) => !arg.startsWith("-"));
  if (nonFlagArgs.length < 2) {
    // Expect at least a mode and one path argument
    return false;
  }

  const modeArg = nonFlagArgs[0];
  const symbolicModePattern = /^[ugoa]*[+-=][rwxstugo]+$/;
  const octalModePattern = /^[0-7]{3,4}$/;

  if (!symbolicModePattern.test(modeArg) && !octalModePattern.test(modeArg)) {
    return false;
  }

  return true;
}

export async function filterUnsafeCommands(
  allToolCalls: ToolCall[],
  config: GraphConfig,
): Promise<{ filteredToolCalls: ToolCall[]; wasFiltered: boolean }> {
  const commandExecutingTools = [
    "shell",
    "grep",
    "view",
    "search_documents_for",
    "get_url_content",
    "str_replace_based_edit_tool",
  ];
  const commandToolCalls = allToolCalls.filter((toolCall) =>
    commandExecutingTools.includes(toolCall.name),
  );

  if (commandToolCalls.length === 0) {
    return { filteredToolCalls: allToolCalls, wasFiltered: false };
  }

  const otherToolCalls = allToolCalls.filter(
    (toolCall) => !commandExecutingTools.includes(toolCall.name),
  );

  const knownSafeCommands = commandToolCalls.filter(isKnownSafeCommand);
  const commandsNeedingEvaluation = commandToolCalls.filter(
    (toolCall) => !isKnownSafeCommand(toolCall),
  );

  let evaluationResult: CommandEvaluationResult = {
    safeCommands: [],
    unsafeCommands: [],
    allCommands: [],
    filteredToolCalls: [],
    wasFiltered: false,
  };

  if (commandsNeedingEvaluation.length > 0) {
    evaluationResult = await evaluateCommands(commandsNeedingEvaluation, config);
  }

  // Log unsafe commands that are being filtered out
  if (evaluationResult.unsafeCommands.length > 0) {
    evaluationResult.unsafeCommands.forEach((evaluation) => {
      logger.warn(`Filtering out UNSAFE command:`, {
        command: evaluation.commandDescription,
        reasoning: evaluation.reasoning,
        riskLevel: evaluation.riskLevel,
      });
    });
  }

  const safeToolCalls = [
    ...knownSafeCommands,
    ...evaluationResult.safeCommands.map((evaluation) => evaluation.toolCall),
  ];

  const filteredToolCalls = [...safeToolCalls, ...otherToolCalls];
  const wasFiltered = safeToolCalls.length !== commandToolCalls.length;

  if (evaluationResult.wasFiltered || wasFiltered) {
    logger.info(
      `Filtered out ${commandToolCalls.length - safeToolCalls.length} unsafe commands`,
    );
  }

  return {
    filteredToolCalls,
    wasFiltered,
  };
}

export { isKnownSafeCommand };
