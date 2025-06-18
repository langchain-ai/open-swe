import { Client } from "@langchain/langgraph-sdk";

export function createLangGraphClient(options?: {
  defaultHeaders?: Record<string, string>;
  includeApiKey?: boolean;
}) {
  if (!process.env.LANGGRAPH_API_URL) {
    throw new Error("LANGGRAPH_API_URL not found");
  }
  if (options?.includeApiKey && !process.env.LANGGRAPH_API_KEY) {
    throw new Error("LANGGRAPH_API_KEY not found");
  }
  return new Client({
    ...(options?.includeApiKey && {
      apiKey: process.env.LANGGRAPH_API_KEY,
    }),
    apiUrl: process.env.LANGGRAPH_API_URL,
    defaultHeaders: options?.defaultHeaders,
  });
}
