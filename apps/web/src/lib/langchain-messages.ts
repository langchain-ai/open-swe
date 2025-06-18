import {
  AIMessage,
  HumanMessage,
  Message,
  ToolMessage,
} from "@langchain/langgraph-sdk";

export function isAIMessageSDK(m: Message): m is AIMessage {
  return m.type === "ai";
}

export function isToolMessageSDK(m: Message): m is ToolMessage {
  return m.type === "tool";
}

export function isHumanMessageSDK(m: Message): m is HumanMessage {
  return m.type === "human";
}
