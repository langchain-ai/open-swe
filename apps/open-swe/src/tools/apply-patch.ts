import { tool } from "@langchain/core/tools";
import { applyPatch } from "diff";
import { GraphState, GraphConfig } from "@open-swe/shared/open-swe/types";
import { readFile, writeFile } from "../utils/read-write.js";
import { fixGitPatch } from "../utils/diff.js";
import { createLogger, LogLevel } from "../utils/logger.js";
import { createApplyPatchToolFields } from "@open-swe/shared/open-swe/tools";
import { getRepoAbsolutePath } from "@open-swe/shared/git";
import { getSandboxSessionOrThrow } from "./utils/get-sandbox-id.js";
import { Sandbox } from "@daytonaio/sdk";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@open-swe/shared/open-swe/local-mode";
import { createShellExecutor } from "../utils/shell-executor/shell-executor.js";
import { join } from "path";
import { v4 as uuidv4 } from "uuid";

type FileOperationResult = {
  success: boolean;
  output: string;
};

const logger = createLogger(LogLevel.INFO, "ApplyPatchTool");

/**
 * Attempts to apply a patch using Git CLI
 * @param sandbox The sandbox session (optional in local mode)
 * @param workDir The working directory
 * @param diffContent The diff content
 * @param config The graph config to determine if in local mode
 * @returns Object with success status and output or error message
 */
async function applyPatchWithGit(
  sandbox: Sandbox | null,
  workDir: string,
  diffContent: string,
  config: GraphConfig,
): Promise<FileOperationResult> {
  // Generate temp patch file path
  const tempPatchFile = isLocalMode(config)
    ? join(workDir, `patch_${uuidv4()}.diff`)
    : `/tmp/patch_${uuidv4()}.diff`;

  try {
    // Create the patch file using unified shell executor
    const executor = createShellExecutor(config);
    const createFileResponse = await executor.executeCommand({
      command: `cat > "${tempPatchFile}" << 'EOF'\n${diffContent}\nEOF`,
      workdir: workDir,
      timeout: 10, // 10 seconds timeout for file creation
      sandbox: sandbox || undefined,
    });

    if (createFileResponse.exitCode !== 0) {
      return {
        success: false,
        output: `Failed to create patch file: ${createFileResponse.result || "Unknown error"}`,
      };
    }

    // Execute git apply with --verbose for detailed error messages
    const response = await executor.executeCommand({
      command: `git apply --verbose "${tempPatchFile}"`,
      workdir: workDir,
      timeout: 30,
      sandbox: sandbox || undefined,
    });

    if (response.exitCode !== 0) {
      return {
        success: false,
        output: `Git apply failed with exit code ${response.exitCode}:\n${response.result || response.artifacts?.stdout || "No error output"}`,
      };
    }

    return {
      success: true,
      output: response.result || "Patch applied successfully",
    };
  } catch (error) {
    return {
      success: false,
      output:
        error instanceof Error
          ? error.message
          : "Unknown error applying patch with git",
    };
  } finally {
    // Clean up temp file using unified shell executor
    try {
      const executor = createShellExecutor(config);
      await executor.executeCommand({
        command: `rm -f "${tempPatchFile}"`,
        workdir: workDir,
        timeout: 5, // 5 seconds timeout for cleanup
        sandbox: sandbox || undefined,
      });
    } catch (cleanupError) {
      logger.warn(`Failed to clean up temp patch file: ${tempPatchFile}`, {
        cleanupError,
      });
    }
  }
}

export function createApplyPatchTool(state: GraphState, config: GraphConfig) {
  const applyPatchTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      const { diff, file_path } = input;
      const workDir = isLocalMode(config)
        ? getLocalWorkingDirectory()
        : getRepoAbsolutePath(state.targetRepository);

      // Get sandbox for sandbox mode (will be undefined for local mode)
      const sandbox = isLocalMode(config)
        ? null
        : await getSandboxSessionOrThrow(input);

      // Read the file using unified readFile function
      const readFileResult = await readFile({
        sandbox,
        filePath: file_path,
        workDir,
        config,
      });

      if (!readFileResult.success) {
        throw new Error(readFileResult.output);
      }

      // Apply the patch using Git CLI for better error messages
      logger.info(`Attempting to apply patch to ${file_path} using Git CLI`);
      const gitResult = await applyPatchWithGit(sandbox, workDir, diff, config);

      const readFileOutput = readFileResult.output;

      // If Git successfully applied the patch, read the updated file and return success
      if (gitResult.success) {
        const readUpdatedResult = await readFile({
          sandbox,
          filePath: file_path,
          workDir,
          config,
        });

        if (!readUpdatedResult.success) {
          throw new Error(
            `Failed to read updated file after applying patch: ${readUpdatedResult.output}`,
          );
        }

        logger.info(`Successfully applied diff to ${file_path} using Git CLI`);
        return {
          result: `Successfully applied diff to \`${file_path}\` and saved changes.`,
          status: "success",
        };
      }

      // If Git failed, fall back to the diff library with detailed error capture
      logger.warn(
        `Git CLI patch application failed: ${gitResult.output}. Falling back to diff library.`,
      );

      let patchedContent: string | false;
      let fixedDiff: string | false = false;
      let errorApplyingPatchMessage: string | undefined;

      try {
        logger.info(`Applying patch to file ${file_path} using diff library`);
        patchedContent = applyPatch(readFileOutput, diff);
      } catch (e) {
        errorApplyingPatchMessage =
          e instanceof Error ? e.message : "Unknown error";
        try {
          logger.warn(
            "Failed to apply patch: Invalid diff. Attempting to fix",
            {
              ...(e instanceof Error
                ? { name: e.name, message: e.message, stack: e.stack }
                : { error: e }),
            },
          );
          const fixedDiff_ = fixGitPatch(diff, {
            [file_path]: readFileOutput,
          });
          patchedContent = applyPatch(readFileOutput, fixedDiff_);
          if (patchedContent) {
            logger.info("Successfully fixed diff and applied patch to file", {
              file_path,
            });
            fixedDiff = fixedDiff_;
          }
        } catch (_) {
          // Combine both Git and diff library error messages for maximum context
          const diffErrMessage =
            e instanceof Error ? e.message : "Unknown error";
          throw new Error(
            `FAILED TO APPLY PATCH: The diff could not be applied to file '${file_path}'.\n\n` +
              `Git Error: ${gitResult.output}\n\n` +
              `Diff Library Error: ${diffErrMessage}`,
          );
        }
      }

      if (patchedContent === false) {
        throw new Error(
          `FAILED TO APPLY PATCH: The diff could not be applied to file '${file_path}'.\n\n` +
            `Git Error: ${gitResult.output}\n\n` +
            `This may be due to an invalid diff format or conflicting changes with the file's current content. ` +
            `Original content length: ${readFileOutput.length}, Diff: ${diff.substring(0, 100)}...`,
        );
      }

      // Write the patched content using unified writeFile function
      const writeFileResult = await writeFile({
        sandbox,
        filePath: file_path,
        content: patchedContent,
        workDir,
        config,
      });

      if (!writeFileResult.success) {
        throw new Error(writeFileResult.output);
      }

      let resultMessage = `Successfully applied diff to \`${file_path}\` and saved changes.`;
      logger.info(resultMessage);
      if (fixedDiff) {
        resultMessage +=
          "\n\nNOTE: The generated diff was NOT formatted properly, and had to be fixed." +
          `\nHere is the error that was thrown when your generated diff was applied:\n<apply-diff-error>\n${errorApplyingPatchMessage}\n</apply-diff-error>` +
          `\nThe diff which was applied is:\n<fixed-diff>\n${fixedDiff}\n</fixed-diff>`;
      }

      // Include Git error for context even on success
      resultMessage += `\n\nGit apply attempt failed with message:\n<git-error>\n${gitResult.output}\n</git-error>`;

      return {
        result: resultMessage,
        status: "success",
      };
    },
    createApplyPatchToolFields(state.targetRepository),
  );
  return applyPatchTool;
}
