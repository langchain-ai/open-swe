import { Chunk, StreamProcessorActions } from "@types";
import { BaseMessage, AIMessage, ToolMessage } from "@langchain/core/messages";
import { saveSession } from "@lib/storage";

const seenMessageIds = new WeakSet<BaseMessage>();

const normalizeToolOutput = (raw: unknown): string => {
  if (typeof raw === 'string') return raw;
  if (Array.isArray(raw)) {
    const text = raw
      .filter((block): block is { type: string; text: string } =>
        typeof block === 'object' &&
        block !== null &&
        (block as any).type === 'text' &&
        typeof (block as any).text === 'string')
      .map((block) => block.text)
      .join('\n');
    if (text) return text;
  }
  return JSON.stringify(raw);
};

export const processStreamUpdate = async (
    chunk: Record<string, any>,
    conversationHistory: { current: BaseMessage[] },
    actions: StreamProcessorActions
  ) => {
    if (!chunk || typeof chunk !== 'object') return;
    for (const nodeName of Object.keys(chunk)) {
      const update = chunk[nodeName];
      if (!update || typeof update !== 'object') continue;
      const messages = (update as { messages?: BaseMessage[] }).messages;
      if (!messages || !Array.isArray(messages) || messages.length === 0) continue;
      const newMessages: BaseMessage[] = [];
      for (const message of messages) {
        if (typeof message !== 'object' || message === null) continue;
        if (seenMessageIds.has(message)) continue;
        seenMessageIds.add(message);
        newMessages.push(message);
      }
      if (newMessages.length === 0) continue;
      conversationHistory.current.push(...newMessages);
      await saveSession('last_session', conversationHistory.current);
      for (const message of newMessages) {
        const type = typeof message.getType === 'function' ? message.getType() : (message as any).type;
        if (type === 'ai') {
          const aiMessage = message as AIMessage;
          if (aiMessage.usage_metadata) {
            actions.updateTokenUsage({
              input: aiMessage.usage_metadata.input_tokens,
              output: aiMessage.usage_metadata.output_tokens,
              total: aiMessage.usage_metadata.total_tokens,
            });
          }
          if (aiMessage.content) {
            let text: string;
            if (typeof aiMessage.content === 'string') {
              text = aiMessage.content;
            } else if (Array.isArray(aiMessage.content)) {
              text = aiMessage.content
                .filter((block): block is { type: 'text'; text: string } =>
                  typeof block === 'object' && block !== null && (block as any).type === 'text')
                .map((block) => block.text)
                .join('');
            } else {
              text = String(aiMessage.content);
            }
            if (text) {
              actions.addMessage({
                author: 'agent',
                chunks: [{ kind: 'text', text }],
              });
            }
          }
          if (aiMessage.tool_calls && aiMessage.tool_calls.length > 0) {
            const toolExecutionChunks: Chunk[] = aiMessage.tool_calls.map((toolCall) => ({
              kind: 'tool-execution',
              toolCallId: toolCall.id,
              toolName: toolCall.name,
              toolArgs: toolCall.args,
              status: 'running',
            }));
            actions.addMessage({
              author: 'system',
              chunks: toolExecutionChunks,
            });
          }
        } else if (type === 'tool') {
          const toolMessage = message as ToolMessage;
          const output = normalizeToolOutput(toolMessage.content);
          const isError = typeof output === 'string' && output.toLowerCase().startsWith('error');
          actions.updateToolExecution({
            toolCallId: toolMessage.tool_call_id,
            status: isError ? 'error' : 'success',
            output,
          });
        }
      }
    }
  };
