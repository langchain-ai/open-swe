import { tool } from "@langchain/core/tools";
import { createLogger, LogLevel } from "../utils/logger.js";
import { GraphState } from "@open-swe/shared/open-swe/types";
import { getSandboxErrorFields } from "../utils/sandbox-error-fields.js";
import {
  createDevServerToolFields,
  formatCurlCommand,
} from "@open-swe/shared/open-swe/tools";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";
import { sleep } from "../utils/sleep.js";
import { z } from "zod";
import { ExecuteResponse } from "@daytonaio/sdk/src/types/ExecuteResponse.js";

const DEFAULT_ENV = {
  // Prevents corepack from showing a y/n download prompt which causes the command to hang
  COREPACK_ENABLE_DOWNLOAD_PROMPT: "0",
};

const logger = createLogger(LogLevel.INFO, "DevServerTool");

const dummyDevServerToolFields = createDevServerToolFields({
  owner: "dummy",
  repo: "dummy",
});

function verifyOneAndNotBothDefined(
  requestCommand: z.infer<
    typeof dummyDevServerToolFields.schema
  >["requestCommand"],
  curlCommand: z.infer<typeof dummyDevServerToolFields.schema>["curlCommand"],
) {
  if (!requestCommand && !curlCommand) {
    throw new Error(
      `One of ${dummyDevServerToolFields.name} requestCommand or curlCommand must be provided. Received undefined for both.`,
    );
  }
  const requestHasValues = !!requestCommand?.command?.length;
  const curlHasValues = !!curlCommand?.url;
  if (requestHasValues && curlHasValues) {
    throw new Error(
      `Only one of ${dummyDevServerToolFields.name} requestCommand or curlCommand must be provided. Received values for both.`,
    );
  }
}

export function createDevServerTool(
  state: Pick<GraphState, "sandboxSessionId" | "targetRepository">,
) {
  const devServerTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      const sandbox = await getSandboxSessionOrThrow(input);
      const sessionId = `dev-server-${Date.now()}`;
      try {
        const {
          serverStartupCommand: {
            command,
            workdir: serverWorkdir,
            waitTime = 5,
          },
          requestCommand,
          curlCommand,
        } = input;

        verifyOneAndNotBothDefined(requestCommand, curlCommand);

        logger.info("Starting dev server");

        await sandbox.process.createSession(sessionId);

        const startResponse = await sandbox.process.executeSessionCommand(
          sessionId,
          {
            command: `cd ${serverWorkdir} && ${command.join(" ")}`,
            runAsync: true,
          },
        );

        if (!startResponse.cmdId) {
          throw new Error("Failed to start dev server");
        }

        await sleep(waitTime * 1000);

        let requestString = "";

        let requestResult: ExecuteResponse;
        if (requestCommand) {
          logger.info(
            `Sending test request: ${requestCommand.command.join(" ")} in ${requestCommand.workdir}`,
          );

          requestString = requestCommand.command.join(" ");
          requestResult = await sandbox.process.executeCommand(
            requestString,
            requestCommand.workdir,
            DEFAULT_ENV,
            requestCommand.timeout ?? 60,
          );
        } else if (curlCommand) {
          logger.info(
            `Sending test request: ${curlCommand.url} with method ${curlCommand.method}`,
          );
          requestString = formatCurlCommand(curlCommand);
          requestResult = await sandbox.process.executeCommand(
            requestString,
            serverWorkdir,
            DEFAULT_ENV,
            curlCommand.timeout ?? 60,
          );
        } else {
          // Should never happen due to the verifyOneAndNotBothDefined function above. Need for type narrowing.
          throw new Error("No request command or curl command provided");
        }

        const logsResponse = await sandbox.process.getSessionCommandLogs(
          sessionId,
          startResponse.cmdId,
        );
        logger.info(`Logs retrieved:`, { logsLength: logsResponse.length });

        await sandbox.process.deleteSession(sessionId);

        const resultParts = [];
        resultParts.push("<run_dev_server_results>");
        resultParts.push(`<command>${command.join(" ")}</command>`);
        resultParts.push(`<request>${requestString}</request>`);
        resultParts.push("");
        resultParts.push("<request_response>");
        resultParts.push(
          requestResult.result || `Exit code: ${requestResult.exitCode}`,
        );
        resultParts.push("</request_response>");
        resultParts.push("");
        resultParts.push("<server_logs>");
        resultParts.push(logsResponse);
        resultParts.push("</server_logs>");
        resultParts.push("</run_dev_server_results>");

        logger.info("Monitor dev server completed", {
          sessionId,
          responseExitCode: requestResult.exitCode,
          logsLength: logsResponse.length,
        });

        return {
          result: resultParts.join("\n"),
          status: "success",
        };
      } catch (e) {
        const errorFields = getSandboxErrorFields(e);
        if (errorFields) {
          const errorResult =
            errorFields.result ?? errorFields.artifacts?.stdout;
          throw new Error(
            `Monitor dev server failed. Exit code: ${errorFields.exitCode}\nError: ${errorResult}`,
          );
        }

        throw e;
      }
    },
    createDevServerToolFields(state.targetRepository),
  );

  return devServerTool;
}
