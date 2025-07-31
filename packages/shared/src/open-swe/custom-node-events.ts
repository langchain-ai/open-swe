export type CustomNodeEvent = {
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

export function isCustomNodeEvent(event: unknown): event is CustomNodeEvent {
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
export const ACCEPTED_PLAN_NODE_ID = "accepted-plan";
export const REQUEST_HELP_NODE_ID = "request-help";

export const INIT_STEPS = [
  "Resuming sandbox",
  "Creating sandbox",
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
export function mapCustomEventsToSteps(events: CustomNodeEvent[]) {
  return INIT_STEPS.flatMap((stepName) => {
    const event = [...events]
      .filter((e) => e.action === stepName)
      .sort(
        (a, b) =>
          new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime(),
      )[0];
    if (!event) return [];
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
    return [];
  }).filter((step) => step.status !== "skipped");
}
