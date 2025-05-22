import { GraphState, GraphConfig, GraphUpdate } from "../types.js";

/**
 * Initializes the session. This ensures there's an active VM session, and that
 * the proper credentials are provided for taking actions on GitHub.
 */
export function generateAction(
  state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  throw new Error("Not implemented");
}
