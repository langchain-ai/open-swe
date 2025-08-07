import { ChatOllama } from "@langchain/community/chat_models/ollama";
import { BaseChatModel } from "@langchain/core/language_models/chat";

export function initOllama(): BaseChatModel {
  const model = new ChatOllama({
    baseUrl: process.env.OLLAMA_API_URL,
    model: process.env.OLLAMA_MODEL,
  });
  return model;
}
