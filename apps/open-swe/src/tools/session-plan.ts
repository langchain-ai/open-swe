import { z } from "zod";

const sessionPlanSchema = z.object({
  plan: z.array(z.string()).describe("The plan to address the user's request."),
});

export const sessionPlanTool = {
  name: "session_plan",
  description: "Call this tool when you are ready to generate a plan.",
  schema: sessionPlanSchema,
};
