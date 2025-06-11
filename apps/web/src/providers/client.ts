import { Client } from "@langchain/langgraph-sdk";

export function createClient(apiUrl: string) {
  return new Client({
    apiUrl,
  });
}
