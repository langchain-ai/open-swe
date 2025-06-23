import { Client } from "@langchain/langgraph-sdk";

export function createClient(apiUrl: string) {
  // This ensures authentication is handled through the server-side proxy

  return new Client({
    apiUrl: apiUrl,
  });
}
