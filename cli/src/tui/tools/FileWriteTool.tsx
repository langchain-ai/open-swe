import { Box } from "ink";
import { DiffView } from "@tui/components/DiffView.js";
import { buildToolPatch } from "@lib/structured-diff.js";
import { argFilePath, getDisplayPath } from "./utils.js";
import type { ToolUI } from "./types.js";

export const FileWriteTool: ToolUI = {
  names: ["write_file", "write"],
  userFacingName: () => "Create",
  renderToolUseMessage: (args, { verbose }) => {
    const filePath = argFilePath(args);
    if (!filePath) return null;
    return verbose ? filePath : getDisplayPath(filePath);
  },
  renderToolResultMessage: (_output, { args }) => {
    if (!args) return null;
    const patch = buildToolPatch("write_file", args);
    if (!patch || patch.hunks.length === 0) return null;
    return (
      <Box flexDirection="column">
        <DiffView hunks={patch.hunks} filePath={patch.filePath} />
      </Box>
    );
  },
};
