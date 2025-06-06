import { PlanItem } from "@/components/plan";

/**
 * Checks if the interrupt args contain plan data
 */
export function isPlanData(args: Record<string, any>): boolean {
  // Check for structured plan data (future format)
  if (args.planItems && Array.isArray(args.planItems)) {
    return args.planItems.every(
      (item: any) =>
        typeof item === "object" &&
        typeof item.index === "number" &&
        typeof item.plan === "string" &&
        typeof item.completed === "boolean",
    );
  }

  // Check for string-based plan data (current format with ::: separators)
  if (args.plan && typeof args.plan === "string") {
    return args.plan.includes(":::");
  }

  // Check for any string arg that looks like a plan with step indicators
  return Object.values(args).some(
    (value) =>
      typeof value === "string" &&
      value.includes(":::") &&
      (value.includes("step") ||
        value.includes("task") ||
        value.includes("action")),
  );
}

/**
 * Parses plan data from interrupt args into PlanItem array
 */
export function parsePlanData(args: Record<string, any>): PlanItem[] {
  // Handle structured plan data (future format)
  if (args.planItems && Array.isArray(args.planItems)) {
    return args.planItems.map((item: any) => ({
      index: item.index,
      plan: item.plan,
      completed: item.completed,
      summary: item.summary,
    }));
  }

  // Handle string-based plan data (current format)
  let planString = "";
  if (args.plan && typeof args.plan === "string") {
    planString = args.plan;
  } else {
    const planValue = Object.values(args).find(
      (value) => typeof value === "string" && value.includes(":::"),
    );
    if (planValue) {
      planString = planValue as string;
    }
  }

  if (!planString) {
    return [];
  }

  const steps = planString
    .split(":::")
    .map((step) => step.trim())
    .filter((step) => step.length > 0);

  return steps.map((step, index) => ({
    index,
    plan: step,
    completed: false, // For MVP, all tasks start as incomplete
    summary: undefined,
  }));
}

/**
 * Gets the key name that contains plan data
 */
export function getPlanKey(args: Record<string, any>): string | null {
  if (args.planItems) return "planItems";
  if (args.plan) return "plan";

  // Find the first key with plan-like data
  const planEntry = Object.entries(args).find(
    ([key, value]) =>
      typeof value === "string" &&
      value.includes(":::") &&
      (value.includes("step") ||
        value.includes("task") ||
        value.includes("action")),
  );

  return planEntry ? planEntry[0] : null;
}
