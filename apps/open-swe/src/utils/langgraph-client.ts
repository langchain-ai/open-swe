import { Client } from "@langchain/langgraph-sdk";

export function createLangGraphClient(options?: {
  defaultHeaders?: Record<string, string>;
  includeApiKey?: boolean;
}) {
  // TODO: Remove the need for this after issues with port are resolved.
  const envApiUrl = [
    process.env.LANGGRAPH_API_URL,
    process.env.LANGGRAPH_URL,
    process.env.LANGGRAPH_PROD_URL,
  ]
    .map((value) => value?.trim())
    .find((value): value is string => Boolean(value && value.length > 0));

  const port = process.env.PORT?.trim();
  const fallbackPort = port && port.length > 0 ? port : "2024";

  const apiUrl = envApiUrl ?? `http://localhost:${fallbackPort}`;
  if (options?.includeApiKey && !process.env.LANGGRAPH_API_KEY) {
    throw new Error("LANGGRAPH_API_KEY not found");
  }
  return new Client({
    ...(options?.includeApiKey && {
      apiKey: process.env.LANGGRAPH_API_KEY,
    }),
    apiUrl,
    defaultHeaders: options?.defaultHeaders,
  });
}
