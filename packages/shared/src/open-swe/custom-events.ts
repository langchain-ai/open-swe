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
    status: "pending" | "success" | "error" | "skipped";
    [key: string]: unknown;
  };
};

export function isCustomEvent(event: unknown): event is CustomEvent {
  return (
    typeof event === "object" &&
    event !== null &&
    "nodeId" in event &&
    "actionId" in event &&
    "action" in event &&
    "data" in event &&
    "createdAt" in event
  );
}
export const INITIALIZE_NODE_ID = "initialize";

export const INIT_STEPS = [
  "Resuming Sandbox",
  "Creating Sandbox",
  "Cloning repository",
  "Pulling latest changes",
  "Configuring git user",
  "Checking out branch",
  "Generating codebase tree",
];

export type Step = {
  name: string;
  status: "waiting" | "generating" | "success" | "error" | "skipped";
  error?: string;
};

/**
 * Maps custom events to step objects for UI rendering. Skipped steps are filtered out.
 */
export function mapCustomEventsToSteps(events: CustomEvent[]) {
  return INIT_STEPS.map((stepName) => {
    const event = [...events]
      .filter((e) => e.action === stepName)
      .sort(
        (a, b) =>
          new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
      )[0];
    if (!event) return { name: stepName, status: "waiting" as const };
    if (event.data.status === "skipped")
      return { name: stepName, status: "skipped" as const };
    if (event.data.status === "pending")
      return { name: stepName, status: "generating" as const };
    if (event.data.status === "success")
      return { name: stepName, status: "success" as const };
    if (event.data.status === "error")
      return {
        name: stepName,
        status: "error" as const,
        error:
          typeof event.data.error === "string" ? event.data.error : undefined,
      };
    return { name: stepName, status: "waiting" as const };
  }).filter((step) => step.status !== "skipped");
}
