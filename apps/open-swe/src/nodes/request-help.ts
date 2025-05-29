import { isAIMessage } from "@langchain/core/messages";
import { GraphState } from "../types.js";
import { HumanInterrupt, HumanResponse } from "@langchain/langgraph/prebuilt";
import { END, interrupt, Command } from "@langchain/langgraph";
import { pauseSandbox, resumeSandbox } from "../utils/sandbox.js";

const constructDescription = (helpRequest: string): string => {
  return `The agent has requested help. Here is the help request:
  
\`\`\`
${helpRequest}
\`\`\``;
};

export async function requestHelp(state: GraphState): Promise<Command> {
  const lastMessage = state.messages[state.messages.length - 1];
  if (!isAIMessage(lastMessage) || !lastMessage.tool_calls?.length) {
    throw new Error("Last message is not an AI message with tool calls.");
  }
  const sandboxSessionId = state.sandboxSessionId;
  if (!sandboxSessionId) {
    throw new Error("Sandbox session ID not found.");
  }
  await pauseSandbox(sandboxSessionId);

  const toolCall = lastMessage.tool_calls[0];

  const interruptInput: HumanInterrupt = {
    action_request: {
      action: "Help Requested",
      args: {},
    },
    config: {
      allow_accept: false,
      allow_edit: false,
      allow_ignore: true,
      allow_respond: true,
    },
    description: constructDescription(toolCall.args.help_request),
  };
  const interruptRes = interrupt<HumanInterrupt[], HumanResponse[]>([
    interruptInput,
  ])[0];

  if (interruptRes.type === "ignore") {
    return new Command({
      goto: END,
    });
  }

  if (interruptRes.type === "response") {
    if (typeof interruptRes.args !== "string") {
      throw new Error("Interrupt response expected to be a string.");
    }
    await resumeSandbox(sandboxSessionId);
    return new Command({
      goto: "generate-action",
      update: {
        messages: [
          {
            role: "tool",
            tool_call_id: toolCall.id,
            content: `Human response: ${interruptRes.args}`,
            status: "success",
          },
        ],
      },
    });
  }

  throw new Error(
    `Invalid interrupt response type. Must be one of 'ignore' or 'response'. Received: ${interruptRes.type}`,
  );
}
