import { Client } from "@langchain/langgraph-sdk";

export function createClient(apiUrl: string) {
  // Use the Next.js API route instead of the direct LangGraph server
  // This ensures authentication is handled through the server-side proxy

  return new Client({
    apiUrl: apiUrl,
  });
}
