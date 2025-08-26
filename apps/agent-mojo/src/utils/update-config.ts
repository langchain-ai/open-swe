import { getConfig } from "@langchain/langgraph";

export function updateConfig(key: string, value: unknown) {
  try {
    const config = getConfig();
    if (!config.configurable) {
      throw new Error("No configurable object found");
    }
    config.configurable[key] = value;
  } catch {
    // no-op
    return;
  }
}
