import { createLogger, LogLevel } from "./logger.js";
import { createCommandSafetyEvaluator } from "../tools/command-safety-evaluator.js";
import { GraphConfig } from "@open-swe/shared/open-swe/types";

const logger = createLogger(LogLevel.INFO, "CommandEvaluation");

export interface CommandEvaluation {
  toolCall: any;
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
  filteredToolCalls: any[];
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

export function getCommandString(toolCall: any): {
  commandString: string;
  commandDescription: string;
} {
  let commandString = "";
  let commandDescription = "";

  if (toolCall.name === "shell") {
    const args = toolCall.args as { command: string[]; workdir?: string };
    commandString = args.command.join(" ");
    commandDescription = `${toolCall.name} - ${commandString}${args.workdir ? ` (in ${args.workdir})` : ""}`;
  } else if (toolCall.name === "grep") {
    const args = toolCall.args as { query: string; file_path?: string };
    commandString = `rg --color=never --line-number --heading -i '${args.query}'${args.file_path ? ` ${args.file_path}` : ""}`;
    commandDescription = `${toolCall.name} - searching for "${args.query}"${args.file_path ? ` in ${args.file_path}` : ""}`;
  } else if (toolCall.name === "view") {
    const args = toolCall.args as { file_path: string };
    commandString = `cat ${args.file_path}`;
    commandDescription = `${toolCall.name} - viewing ${args.file_path}`;
  } else if (toolCall.name === "search_documents_for") {
    const args = toolCall.args as { query: string; file_path?: string };
    commandString = `search for "${args.query}"${args.file_path ? ` in ${args.file_path}` : ""}`;
    commandDescription = `${toolCall.name} - searching documents for "${args.query}"${args.file_path ? ` in ${args.file_path}` : ""}`;
  } else if (toolCall.name === "get_url_content") {
    const args = toolCall.args as { url: string };
    commandString = `curl ${args.url}`;
    commandDescription = `${toolCall.name} - fetching content from ${args.url}`;
  }

  return { commandString, commandDescription };
}

export async function evaluateCommands(
  commandToolCalls: any[],
  config: GraphConfig,
): Promise<CommandEvaluationResult> {
  const commandExecutingTools = [
    "shell",
    "grep",
    "view",
    "search_documents_for",
    "get_url_content",
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

        const result = JSON.parse(evaluation.result);
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

export async function filterUnsafeCommands(
  allToolCalls: any[],
  config: GraphConfig,
): Promise<{ filteredToolCalls: any[]; wasFiltered: boolean }> {
  const commandExecutingTools = [
    "shell",
    "grep",
    "view",
    "search_documents_for",
    "get_url_content",
  ];
  const commandToolCalls = allToolCalls.filter((toolCall) =>
    commandExecutingTools.includes(toolCall.name),
  );

  if (commandToolCalls.length === 0) {
    return { filteredToolCalls: allToolCalls, wasFiltered: false };
  }

  const evaluationResult = await evaluateCommands(commandToolCalls, config);

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

  if (evaluationResult.wasFiltered) {
    logger.info(
      `Filtered out ${allToolCalls.length - evaluationResult.filteredToolCalls.length} unsafe commands`,
    );
  }

  return {
    filteredToolCalls: evaluationResult.filteredToolCalls,
    wasFiltered: evaluationResult.wasFiltered,
  };
}
