import { z } from "zod";

const updatePlanSchema = z.object({
  update_plan_reasoning: z
    .string()
    .describe(
      "The reasoning for why you are updating the plan. This should include context which will be useful when actually updating the plan, such as what plan items to update, edit, or remove, along with any other context that would be useful when updating the plan.",
    ),
});

export const updatePlanTool = {
  name: "update_plan",
  schema: updatePlanSchema,
  description:
    "Call this tool to update the current plan. This should ONLY be called if you want to remove, edit, or add plan items to the current plan." +
    "\nDo NOT call this tool to mark a plan item as completed, or add a summary." +
    "\nYou can not edit/remove completed plan items. This tool can only be used to update/add/remove plan items from the remaining and current plan items." +
    "\nThe reasoning you pass to this tool will be used in the step that actually updates the plan, so ensure it is useful and concise.",
};
