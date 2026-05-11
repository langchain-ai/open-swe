import { Box } from "ink";
import { DiffView } from "@tui/components/DiffView.js";
import {
  buildToolPatch,
  parseStructuredDiffOutput,
} from "@lib/structured-diff.js";
import { argFilePath, argString, getDisplayPath } from "./utils.js";
import type { ToolUI } from "./types.js";

export const FileEditTool: ToolUI = {
  names: ["edit_file", "edit", "apply_diff"],
  userFacingName: (args) => {
    if (args && argString(args, "old_string") === "") {
      return "Create";
    }
    return "Update";
  },
  renderToolUseMessage: (args, { verbose }) => {
    const filePath = argFilePath(args);
    if (!filePath) return null;
    return verbose ? filePath : getDisplayPath(filePath);
  },
  renderToolResultMessage: (output, { args }) => {
    if (args) {
      const patch = buildToolPatch("edit_file", args);
      if (patch && patch.hunks.length > 0) {
        return (
          <Box flexDirection="column">
            <DiffView hunks={patch.hunks} filePath={patch.filePath} />
          </Box>
        );
      }
    }
    if (output) {
      const hunks = parseStructuredDiffOutput(output);
      if (hunks) {
        return (
          <Box flexDirection="column">
            <DiffView hunks={hunks} />
          </Box>
        );
      }
    }
    return null;
  },
};
