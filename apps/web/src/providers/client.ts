import { Client } from "@langchain/langgraph-sdk";

export function createClient(apiUrl: string) {
  // Use the Next.js API route instead of the direct LangGraph server
  // This ensures authentication is handled through the server-side proxy
  const nextApiUrl =
    typeof window !== "undefined" ? `${window.location.origin}/api` : "/api";

  return new Client({
    apiUrl: nextApiUrl,
  });
}
