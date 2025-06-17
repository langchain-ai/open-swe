export type CustomEvent = {
  /**
   * A UUID for the node the action is associated with.
   */
  nodeId: string;
  /**
   * A UUID for the action the event is associated with.
   */
  actionId: string;
  action: string;
  createdAt: string;
  data: {
    status: "pending" | "success" | "error";
    [key: string]: unknown;
  }
}

export function isCustomEvent(event: unknown): event is CustomEvent {
  return typeof event === "object" && event !== null && "nodeId" in event && "actionId" in event && "action" in event && "data" in event && "createdAt" in event;
}

export const INITIALIZE_NODE_ID = "initialize";
