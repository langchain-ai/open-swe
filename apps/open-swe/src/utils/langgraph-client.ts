import { Client } from "@langchain/langgraph-sdk";

export function createLangGraphClient(options?: {
  defaultHeaders?: Record<string, string>;
  includeApiKey?: boolean;
}) {
  const port = process.env.PORT ?? "2024";
  if (options?.includeApiKey && !process.env.LANGGRAPH_API_KEY) {
    throw new Error("LANGGRAPH_API_KEY not found");
  }
  return new Client({
    ...(options?.includeApiKey && {
      apiKey: process.env.LANGGRAPH_API_KEY,
    }),
    apiUrl: `http://localhost:${port}`,
    defaultHeaders: options?.defaultHeaders,
  });
}
