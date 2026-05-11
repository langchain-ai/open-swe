import { Fragment } from "react";
import { Text } from "ink";
import { MessageResponse } from "@tui/components/MessageResponse.js";
import { themeColor } from "@tui/theme.js";
import { argString, getDisplayPath } from "./utils.js";
import type { ToolUI } from "./types.js";

export const GrepTool: ToolUI = {
  names: ["grep", "search"],
  userFacingName: () => "Search",
  renderToolUseMessage: (args, { verbose }) => {
    const pattern = argString(args, "pattern") || argString(args, "query");
    const path = argString(args, "path");
    const glob = argString(args, "glob");
    if (!pattern) return null;
    const where = path ? (verbose ? path : getDisplayPath(path)) : null;
    const parts: (string | null)[] = [pattern];
    if (where && where !== "/") parts.push(`in ${where}`);
    if (glob) parts.push(`(${glob})`);
    return parts.filter(Boolean).join(" ");
  },
  renderToolResultMessage: (output) => {
    if (!output) return null;
    const trimmed = output.trim();
    if (trimmed.startsWith("No matches")) {
      return (
        <MessageResponse height={1}>
          <Text color={themeColor("inactive")}>No matches</Text>
        </MessageResponse>
      );
    }
    const fileLines = trimmed
      .split("\n")
      .filter((line) => line.endsWith(":")).length;
    const matchLines = trimmed
      .split("\n")
      .filter((line) => /^\s+\d+: /.test(line)).length;
    return (
      <MessageResponse height={1}>
        <Text>
          Found <Text bold>{matchLines}</Text>{" "}
          {matchLines === 1 ? "match" : "matches"}
          {fileLines > 0 ? (
            <Fragment>
              {" in "}
              <Text bold>{fileLines}</Text> {fileLines === 1 ? "file" : "files"}
            </Fragment>
          ) : null}
        </Text>
      </MessageResponse>
    );
  },
};
