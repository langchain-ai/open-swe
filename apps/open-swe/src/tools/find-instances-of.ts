import { tool } from "@langchain/core/tools";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { TIMEOUT_SEC } from "@open-swe/shared/constants";
import { createFindInstancesOfToolFields } from "@open-swe/shared/open-swe/tools";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { z } from "zod";
import { wrapScript } from "../utils/wrap-script.js";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";

const logger = createLogger(LogLevel.INFO, "FindInstancesOfTool");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

export function createFindInstancesOfTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
) {
  const findInstancesOfFields = createFindInstancesOfToolFields(
    state.targetRepository,
  );
  const formatFindInstancesOfCommand = (
    input: z.infer<typeof findInstancesOfFields.schema>,
  ): string[] => {
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

    // For literal string matching (not regex)
    args.push("--fixed-strings");

    // Add the search query as the last argument (ensure it's properly quoted)
    const formattedQuery = `'${input.query.replace(/^'|'$/g, "")}'`;
    args.push(formattedQuery);

    return args;
  };

  const findInstancesOfTool = tool(
    async (
      input: z.infer<typeof findInstancesOfFields.schema>,
    ): Promise<{ result: string; status: "success" | "error" }> => {
      try {
        const sandbox = await getSandboxSessionOrThrow(input);

        const repoRoot = getRepoAbsolutePath(state.targetRepository);
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
          successResult = `Exit code 1. No results found.\n\n${response.result}`;
        } else if (response.exitCode > 1) {
          logger.error("Failed to run find_instances_of command", {
            error: response.result,
            error_result: response,
            input,
          });
          throw new Error("Command failed. Exit code: " + response.exitCode);
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
          throw new Error("Command failed. Exit code: " + errorFields.exitCode);
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
    findInstancesOfFields,
  );

  return findInstancesOfTool;
}
