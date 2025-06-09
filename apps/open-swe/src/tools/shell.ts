import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { Sandbox } from "@daytonaio/sdk";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getConfig, getCurrentTaskInput } from "@langchain/langgraph";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { daytonaClient } from "../utils/sandbox.js";
import { SANDBOX_ROOT_DIR, TIMEOUT_SEC } from "@open-swe/shared/constants";
import { getGenUIMessageIdAndContent } from "../utils/get-gen-ui-message-id.js";
import { typedUi } from "@langchain/langgraph-sdk/react-ui/server";

const logger = createLogger(LogLevel.INFO, "ShellTool");

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

const shellToolSchema = z.object({
  command: z.array(z.string()).describe("The command to run"),
  workdir: z
    .string()
    .default(SANDBOX_ROOT_DIR)
    .describe(
      `The working directory for the command. Ensure this path is NOT included in any command arguments, as it will be added automatically. Defaults to '${SANDBOX_ROOT_DIR}' as this is the root directory of the sandbox.`,
    ),
  timeout: z
    .number()
    .optional()
    .default(TIMEOUT_SEC)
    .describe(
      "The maximum time to wait for the command to complete in seconds.",
    ),
});

export const shellTool = tool(
  async (input): Promise<{ result: string; status: "success" | "error" }> => {
    const { command, workdir, timeout } = input;

    let sandbox: Sandbox | undefined;
    const state = getCurrentTaskInput<GraphState>();
    const { sandboxSessionId } = state;
    if (!sandboxSessionId) {
      logger.error("FAILED TO RUN COMMAND: No sandbox session ID provided", {
        input,
      });
      throw new Error("FAILED TO RUN COMMAND: No sandbox session ID provided");
    }

    const {
      id: genUIMessageId,
      content: lastMessageContent,
      aiMessageId,
    } = getGenUIMessageIdAndContent(state);
    const ui = typedUi(getConfig());
    try {
      sandbox = await daytonaClient().get(sandboxSessionId);
      const response = await sandbox.process.executeCommand(
        command.join(" "),
        workdir,
        DEFAULT_ENV,
        timeout ?? TIMEOUT_SEC,
      );

      if (response.exitCode !== 0) {
        logger.error("Failed to run command", {
          error: response.result,
          error_result: response,
          input,
        });
        ui.push({
          id: genUIMessageId,
          name: "action-step",
          props: {
            actionType: "shell",
            status: "done",
            success: false,
            command: command.join(" "),
            workdir,
            output: response.result,
            errorCode: response.exitCode,
            reasoningText: lastMessageContent,
          },
          metadata: {
            ai_message_id: aiMessageId,
          },
        });
        throw new Error(
          `Command failed. Exit code: ${response.exitCode}\nResult: ${response.result}\nStdout:\n${response.artifacts?.stdout}`,
        );
      }

      ui.push({
        id: genUIMessageId,
        name: "action-step",
        props: {
          actionType: "shell",
          status: "done",
          success: true,
          command: command.join(" "),
          workdir,
          output: response.result,
          reasoningText: lastMessageContent,
        },
        metadata: {
          ai_message_id: aiMessageId,
        },
      });
      return {
        result: response.result,
        status: "success",
      };
    } catch (e) {
      const errorFields = getSandboxErrorFields(e);
      if (errorFields) {
        const errorMessage = `Command failed. Exit code: ${errorFields.exitCode}\nError: ${errorFields.result}\nStdout:\n${errorFields.artifacts?.stdout}`;
        logger.error("Failed to run command", {
          input,
          error: errorFields,
        });
        ui.push({
          id: genUIMessageId,
          name: "action-step",
          props: {
            actionType: "shell",
            status: "done",
            success: false,
            command: command.join(" "),
            workdir,
            output: errorFields.result,
            errorCode: errorFields.exitCode,
            reasoningText: lastMessageContent,
          },
          metadata: {
            ai_message_id: aiMessageId,
          },
        });
        throw new Error(errorMessage);
      }

      const errorMessage =
        "Failed to run command: " +
        (e instanceof Error ? e.message : "Unknown error");
      logger.error(errorMessage, {
        error: e,
        input,
      });
      ui.push({
        id: genUIMessageId,
        name: "action-step",
        props: {
          actionType: "shell",
          status: "done",
          success: false,
          command: command.join(" "),
          workdir,
          output: errorMessage,
          errorCode: undefined,
          reasoningText: lastMessageContent,
        },
        metadata: {
          ai_message_id: aiMessageId,
        },
      });
      throw new Error(errorMessage);
    }
  },
  {
    name: "shell",
    description: "Runs a shell command, and returns its output.",
    schema: shellToolSchema,
  },
);
