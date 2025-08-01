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
import { getLocalShellExecutor } from "../utils/local-shell-executor.js";
import { promises as fs } from "fs";
import { join, isAbsolute } from "path";

const logger = createLogger(LogLevel.INFO, "ApplyPatchTool");

/**
 * Local version of readFile using Node.js fs
 */
async function readFileLocal(
  filePath: string,
  workDir: string,
): Promise<{
  success: boolean;
  output: string;
}> {
  try {
    const fullPath = isAbsolute(filePath) ? filePath : join(workDir, filePath);
    const content = await fs.readFile(fullPath, "utf-8");
    return {
      success: true,
      output: content,
    };
  } catch (error: any) {
    if (error.code === "ENOENT") {
      // File doesn't exist, create it
      try {
        const fullPath = isAbsolute(filePath)
          ? filePath
          : join(workDir, filePath);
        await fs.writeFile(fullPath, "", "utf-8");
        return {
          success: true,
          output: "",
        };
      } catch (createError: any) {
        return {
          success: false,
          output: `FAILED TO CREATE FILE '${filePath}'. Error: ${createError.message}`,
        };
      }
    }
    return {
      success: false,
      output: `FAILED TO READ FILE '${filePath}'. Error: ${error.message}`,
    };
  }
}

/**
 * Local version of writeFile using Node.js fs
 */
async function writeFileLocal(
  filePath: string,
  content: string,
  workDir: string,
): Promise<{
  success: boolean;
  output: string;
}> {
  try {
    const fullPath = isAbsolute(filePath) ? filePath : join(workDir, filePath);
    await fs.writeFile(fullPath, content, "utf-8");
    return {
      success: true,
      output: `Successfully wrote file '${filePath}'.`,
    };
  } catch (error: any) {
    return {
      success: false,
      output: `FAILED TO WRITE FILE '${filePath}'. Error: ${error.message}`,
    };
  }
}

/**
 * Local version of applyPatchWithGit using LocalShellExecutor
 */
async function applyPatchWithGitLocal(
  workDir: string,
  diffContent: string,
): Promise<{ success: boolean; output: string }> {
  const tempPatchFile = join(
    workDir,
    `patch_${Date.now()}_${Math.random().toString(36).substring(2)}.diff`,
  );

  try {
    // Create the patch file locally
    await fs.writeFile(tempPatchFile, diffContent, "utf-8");

    // Execute git apply with --verbose for detailed error messages
    const executor = getLocalShellExecutor(workDir);
    const response = await executor.executeCommand(
      `git apply --verbose "${tempPatchFile}"`,
      workDir,
      {},
      30, // 30 seconds timeout
      true, // localMode
    );

    // Clean up temp file
    try {
      await fs.unlink(tempPatchFile);
    } catch (cleanupError) {
      logger.warn(`Failed to clean up temp patch file: ${tempPatchFile}`, {
        cleanupError,
      });
    }

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
    // Clean up temp file on error
    try {
      await fs.unlink(tempPatchFile);
    } catch (cleanupError) {
      logger.warn(`Failed to clean up temp patch file: ${tempPatchFile}`, {
        cleanupError,
      });
    }

    return {
      success: false,
      output:
        error instanceof Error
          ? error.message
          : "Unknown error applying patch with git",
    };
  }
}

/**
 * Attempts to apply a patch using Git CLI
 * @param sandbox The sandbox session
 * @param workDir The working directory
 * @param diffContent The diff content
 * @returns Object with success status and output or error message
 */
async function applyPatchWithGit(
  sandbox: Sandbox,
  workDir: string,
  diffContent: string,
): Promise<{ success: boolean; output: string }> {
  const tempPatchFile = `/tmp/patch_${Date.now()}_${Math.random().toString(36).substring(2)}.diff`;

  try {
    // Create the patch file in the sandbox
    const createFileResponse = await sandbox.process.executeCommand(
      `cat > "${tempPatchFile}" << 'EOF'\n${diffContent}\nEOF`,
      workDir,
      {},
      10, // 10 seconds timeout for file creation
    );

    if (createFileResponse.exitCode !== 0) {
      return {
        success: false,
        output: `Failed to create patch file: ${createFileResponse.result || "Unknown error"}`,
      };
    }

    // Execute git apply with --verbose for detailed error messages
    const response = await sandbox.process.executeCommand(
      `git apply --verbose "${tempPatchFile}"`,
      workDir,
      {},
      30, // 30 seconds timeout
    );

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
  }
}

export function createApplyPatchTool(state: GraphState, config: GraphConfig) {
  const applyPatchTool = tool(
    async (input): Promise<{ result: string; status: "success" | "error" }> => {
      const { diff, file_path } = input;
      const workDir = isLocalMode(config)
        ? getLocalWorkingDirectory()
        : getRepoAbsolutePath(state.targetRepository);

      let readFileResult;
      let gitResult;

      if (isLocalMode(config)) {
        // Local mode: use local file operations
        readFileResult = await readFileLocal(file_path, workDir);
        if (!readFileResult.success) {
          throw new Error(readFileResult.output);
        }

        // First try to apply the patch using Git CLI for better error messages
        logger.info(
          `Attempting to apply patch to ${file_path} using Git CLI (local mode)`,
        );
        gitResult = await applyPatchWithGitLocal(workDir, diff);
      } else {
        // Sandbox mode: use existing sandbox operations
        const sandbox = await getSandboxSessionOrThrow(input);

        readFileResult = await readFile({
          sandbox,
          filePath: file_path,
          workDir,
        });
        if (!readFileResult.success) {
          throw new Error(readFileResult.output);
        }

        // First try to apply the patch using Git CLI for better error messages
        logger.info(
          `Attempting to apply patch to ${file_path} using Git CLI (sandbox mode)`,
        );
        gitResult = await applyPatchWithGit(sandbox, workDir, diff);
      }

      const readFileOutput = readFileResult.output;

      // If Git successfully applied the patch, read the updated file and return success
      if (gitResult.success) {
        let readUpdatedResult;

        if (isLocalMode(config)) {
          readUpdatedResult = await readFileLocal(file_path, workDir);
        } else {
          const sandbox = await getSandboxSessionOrThrow(input);
          readUpdatedResult = await readFile({
            sandbox,
            filePath: file_path,
            workDir,
          });
        }

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

      let writeFileResult;

      if (isLocalMode(config)) {
        writeFileResult = await writeFileLocal(
          file_path,
          patchedContent,
          workDir,
        );
      } else {
        const sandbox = await getSandboxSessionOrThrow(input);
        writeFileResult = await writeFile({
          sandbox,
          filePath: file_path,
          content: patchedContent,
          workDir,
        });
      }

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
