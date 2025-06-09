import { PlanItem } from "@open-swe/shared/open-swe/types";
import {
  PLAN_INTERRUPT_ACTION_TITLE,
  PLAN_INTERRUPT_DELIMITER,
} from "@open-swe/shared/constants";

/**
 * Checks if the interrupt args contain plan data
 */
export function isPlanData(
  args: Record<string, any>,
  actionName: string,
): boolean {
  return !!(
    actionName === PLAN_INTERRUPT_ACTION_TITLE &&
    args.plan &&
    typeof args.plan === "string" &&
    args.plan?.length > 0
  );
}

/**
 * Parses plan data from interrupt args into PlanItem array
 */
export function parsePlanData(args: Record<string, any>): PlanItem[] {
  if (!args.plan) return [];
  if (typeof args.plan !== "string") return [];
  if (!args.plan.includes(PLAN_INTERRUPT_DELIMITER)) {
    // if args.plan is defined, but does not include the delimiter, it is a single plan item
    return [
      {
        index: 0,
        plan: args.plan,
        completed: false,
        summary: undefined,
      },
    ];
  }
  const planItems = args.plan
    .split(PLAN_INTERRUPT_DELIMITER)
    .map((item: string, index: number) => ({
      index,
      plan: item,
      completed: false,
      summary: undefined,
    }));
  return planItems;
}

/**
 * Gets the key name that contains plan data
 */
export function getPlanKey(args: Record<string, any>): string | null {
  if (args.plan && typeof args.plan === "string") return "plan";
  return null;
}
