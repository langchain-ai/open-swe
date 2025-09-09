import { Client } from "@langchain/langgraph-sdk";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";

export function createClient(apiUrl: string) {
  const defaultHeaders =
    process.env.NEXT_PUBLIC_OPEN_SWE_LOCAL_MODE === "true"
      ? { [LOCAL_MODE_HEADER]: "true" }
      : undefined;

  return new Client({
    apiUrl,
    defaultHeaders,
  });
}
