import { Text } from "ink";
import { MessageResponse } from "@tui/components/MessageResponse.js";
import { argFilePath, getDisplayPath } from "./utils.js";
import type { ToolUI } from "./types.js";

export const LSTool: ToolUI = {
  names: ["ls", "list_files"],
  userFacingName: () => "List",
  renderToolUseMessage: (args, { verbose }) => {
    const path = argFilePath(args);
    const directory =
      (typeof args.directory === "string" ? args.directory : "") || path;
    if (!directory) return ".";
    return verbose ? directory : getDisplayPath(directory);
  },
  renderToolResultMessage: (output) => {
    if (!output) return null;
    const entries = output.split("\n").filter((line) => line.trim().length > 0);
    return (
      <MessageResponse height={1}>
        <Text>
          Listed <Text bold>{entries.length}</Text>{" "}
          {entries.length === 1 ? "entry" : "entries"}
        </Text>
      </MessageResponse>
    );
  },
};
