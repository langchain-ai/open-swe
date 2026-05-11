import { Text } from "ink";
import { MessageResponse } from "@tui/components/MessageResponse.js";
import { argFilePath, getDisplayPath } from "./utils.js";
import type { ToolUI } from "./types.js";

export const FileReadTool: ToolUI = {
  names: ["read_file", "read"],
  userFacingName: () => "Read",
  renderToolUseMessage: (args, { verbose }) => {
    const filePath = argFilePath(args);
    if (!filePath) return null;
    return verbose ? filePath : getDisplayPath(filePath);
  },
  renderToolResultMessage: (output) => {
    if (!output) return null;
    const numLines = output.split("\n").length;
    return (
      <MessageResponse height={1}>
        <Text>
          Read <Text bold>{numLines}</Text> {numLines === 1 ? "line" : "lines"}
        </Text>
      </MessageResponse>
    );
  },
};
