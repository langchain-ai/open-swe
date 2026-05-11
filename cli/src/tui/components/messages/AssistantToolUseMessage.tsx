import { Box, Text } from "ink";
import { ToolUseLoader } from "@tui/components/ToolUseLoader.js";
import { MessageResponse } from "@tui/components/MessageResponse.js";
import { themeColor } from "@tui/theme.js";
import { findToolByName } from "@tui/tools/index.js";
import type { Chunk } from "@types";

type Props = {
  chunk: Chunk;
};

const FALLBACK_RESULT_PREVIEW_LINES = 12;

/**
 * Renders the assistant's tool call header (e.g. `Bash(npm test)`) followed by
 * the tool result body indented under a `⎿` glyph. Per-tool rendering lives in
 * `src/tui/tools/<Tool>.tsx`; this file just orchestrates layout + status.
 */
export const AssistantToolUseMessage = ({ chunk }: Props) => {
  const args = chunk.toolArgs ?? {};
  const status = chunk.status ?? "running";
  const isError = status === "error";
  const isUnresolved = status === "running";

  const tool = findToolByName(chunk.toolName);
  const userFacingName = tool?.userFacingName(args) ?? chunk.toolName ?? "Tool";
  const argsNode =
    tool?.renderToolUseMessage?.(args, { verbose: false }) ??
    defaultArgsLabel(args);

  const subtle = themeColor("subtle");

  return (
    <Box flexDirection="column" marginTop={1} width="100%">
      <Box flexDirection="row">
        <ToolUseLoader isError={isError} isUnresolved={isUnresolved} />
        <Box flexShrink={0}>
          <Text bold>{userFacingName}</Text>
          {argsNode !== "" && argsNode !== null ? (
            <>
              <Text color={subtle}>(</Text>
              <Text>{argsNode}</Text>
              <Text color={subtle}>)</Text>
            </>
          ) : null}
        </Box>
      </Box>
      <ToolUseBody chunk={chunk} />
    </Box>
  );
};

const ToolUseBody = ({ chunk }: { chunk: Chunk }) => {
  const status = chunk.status ?? "running";
  const args = chunk.toolArgs ?? {};
  const tool = findToolByName(chunk.toolName);

  if (status === "running") {
    return (
      <MessageResponse height={1}>
        <Text color={themeColor("inactive")}>Running…</Text>
      </MessageResponse>
    );
  }

  if (status === "error") {
    const output = chunk.output ?? "";
    const custom = tool?.renderToolErrorMessage?.(output, { args });
    if (custom !== null && custom !== undefined) return <>{custom}</>;
    if (!output) return null;
    return (
      <MessageResponse>
        {output
          .split("\n")
          .slice(0, FALLBACK_RESULT_PREVIEW_LINES)
          .map((line, idx) => (
            <Text key={idx} color={themeColor("error")}>
              {line || " "}
            </Text>
          ))}
      </MessageResponse>
    );
  }

  // status === 'success'
  const output = chunk.output ?? "";
  const custom = tool?.renderToolResultMessage?.(output, { args });
  if (custom !== null && custom !== undefined) return <>{custom}</>;
  if (!output || output.startsWith("Successfully")) return null;
  return (
    <MessageResponse>
      {output
        .split("\n")
        .slice(0, FALLBACK_RESULT_PREVIEW_LINES)
        .map((line, idx) => (
          <Text key={idx} color={themeColor("inactive")}>
            {line || " "}
          </Text>
        ))}
    </MessageResponse>
  );
};

const defaultArgsLabel = (args: Record<string, unknown>): string => {
  const entries = Object.entries(args).filter(
    ([, v]) => v !== undefined && v !== null,
  );
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
};
