import { Box } from "ink";
import { AssistantTextMessage } from "./messages/AssistantTextMessage.js";
import { AssistantToolUseMessage } from "./messages/AssistantToolUseMessage.js";
import { UserPromptMessage } from "./messages/UserPromptMessage.js";
import { SystemTextMessage } from "./messages/SystemTextMessage.js";
import { ErrorMessage } from "./messages/ErrorMessage.js";
import type { Message as MessageType, Chunk } from "@types";

type Props = {
  message: MessageType;
};

const renderChunk = (
  chunk: Chunk,
  idx: number,
  author: MessageType["author"],
  timestamp?: string,
  isFirstAssistantChunk?: boolean,
) => {
  if (chunk.kind === "tool-execution") {
    return (
      <AssistantToolUseMessage key={chunk.toolCallId ?? idx} chunk={chunk} />
    );
  }
  if (chunk.kind === "error") {
    return <ErrorMessage key={idx} text={chunk.text ?? ""} />;
  }
  if (chunk.kind === "list" || chunk.kind === "code") {
    const lines = chunk.lines ?? [];
    const text = "```\n" + lines.join("\n") + "\n```";
    return (
      <AssistantTextMessage
        key={idx}
        text={text}
        shouldShowDot={isFirstAssistantChunk}
      />
    );
  }
  const text = chunk.text ?? "";
  if (author === "user") {
    return <UserPromptMessage key={idx} text={text} timestamp={timestamp} />;
  }
  if (author === "agent") {
    return (
      <AssistantTextMessage
        key={idx}
        text={text}
        shouldShowDot={isFirstAssistantChunk}
      />
    );
  }
  return <SystemTextMessage key={idx} text={text} />;
};

export const Message = ({ message }: Props) => {
  const firstAssistantChunkIdx = message.chunks.findIndex(
    (c) => c.kind === "text" || c.kind === "code" || c.kind === "list",
  );
  return (
    <Box flexDirection="column">
      {message.chunks.map((chunk, idx) =>
        renderChunk(
          chunk,
          idx,
          message.author,
          message.timestamp,
          message.author === "agent" && idx === firstAssistantChunkIdx,
        ),
      )}
    </Box>
  );
};
