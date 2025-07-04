import { tool } from "@langchain/core/tools";
import { Sandbox } from "@daytonaio/sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getCurrentTaskInput } from "@langchain/langgraph";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { daytonaClient } from "../utils/sandbox.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { createFindInstancesOfToolFields } from "@open-swe/shared/open-swe/tools";
import { getRepoAbsolutePath } from "@open-swe/shared/git";

const wrapScript = (command: string): string => {
  return `script --return --quiet -c "$(cat <<'OPEN_SWE_X'
${command}
OPEN_SWE_X
)" /dev/null`;
};

const logger = createLogger(LogLevel.INFO, "FindInstancesOfTool");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

interface FindInstancesOfInput {
  query: string;
  case_sensitive?: boolean;
  match_word?: boolean;
  exclude_files?: string;
  include_files?: string;
}

function formatFindInstancesOfCommand(input: FindInstancesOfInput): string[] {
  const args = ["rg"];

  // Always include these flags for consistent output
  args.push("--color", "never", "--line-number", "--heading");
  
  // Add context lines (3 above and 3 below)
  args.push("-A", "3", "-B", "3");
  
  // Handle case sensitivity
  if (!input.case_sensitive) {
    args.push("-i");
  }
  
  // Handle word matching
  if (input.match_word) {
    args.push("--word-regexp");
  }
  
  // Handle file inclusion/exclusion patterns
  if (input.exclude_files) {
    args.push("-g", `!${input.exclude_files}`);
  }
  
  if (input.include_files) {
    args.push("-g", input.include_files);
  }
  
  // Add the search query as the last argument
  args.push(input.query);
  
  return args;
}

export function createFindInstancesOfTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
) {
  const findInstancesOfTool = tool(
    async (input: FindInstancesOfInput): Promise<{ result: string; status: "success" | "error" }> => {
      let sandbox: Sandbox | undefined;
      try {
        const state = getCurrentTaskInput<GraphState>();
        const { sandboxSessionId } = state;
        if (!sandboxSessionId) {
          logger.error(
            "FAILED TO RUN COMMAND: No sandbox session ID provided",
            {
              input,
            },
          );
          throw new Error(
            "FAILED TO RUN COMMAND: No sandbox session ID provided",
          );
        }

        const repoRoot = getRepoAbsolutePath(state.targetRepository);

        sandbox = await daytonaClient().get(sandboxSessionId);
        const command = formatFindInstancesOfCommand(input);
        logger.info("Running find_instances_of command", {
          command: command.join(" "),
          repoRoot,
        });
        
        const response = await sandbox.process.executeCommand(
          wrapScript(command.join(" ")),
          repoRoot,
          DEFAULT_ENV,
          TIMEOUT_SEC,
        );

        let successResult = response.result;

        if (
          response.exitCode === 1 ||
          (response.exitCode === 127 && response.result.startsWith("sh: 1: "))
        ) {
          logger.info("Exit code 1. no results found", {
            ...response,
          });
          successResult = `Exit code 1. No results found.
        } else if (response.exitCode > 1) {
          logger.error("Failed to run find_instances_of command", {
            error: response.result,
            error_result: response,
            input,
          });
          throw new Error(
            "Command failed. Exit code: " + response.exitCode + "
          );
        }

        return {
          result: successResult,
          status: "success",
        };
      } catch (e) {
        const errorFields = getSandboxErrorFields(e);
        if (errorFields) {
          logger.error("Failed to run find_instances_of command", {
            input,
            error: errorFields,
          });
          throw new Error(
            `Command failed. Exit code: ${errorFields.exitCode}
          );
        }

        logger.error(
          "Failed to run find_instances_of command: " +
            (e instanceof Error ? e.message : "Unknown error"),
          {
            error: e,
            input,
          },
        );
        throw new Error(
          "FAILED TO RUN FIND_INSTANCES_OF COMMAND: " +
            (e instanceof Error ? e.message : "Unknown error"),
        );
      }
    },
    createFindInstancesOfToolFields(state.targetRepository),
  );

  return findInstancesOfTool;
}





