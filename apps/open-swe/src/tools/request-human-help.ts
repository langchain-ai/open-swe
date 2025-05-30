import { z } from "zod";

const requestHumanHelpSchema = z.object({
  help_request: z
    .string()
    .describe(
      "The help request to send to the human. Should be concise, but descriptive.",
    ),
});

export const requestHumanHelpTool = {
  name: "request_human_help",
  schema: requestHumanHelpSchema,
  description:
    "Use this tool to request help from the human. This should only be called if you are stuck, and you are unable to continue. This will pause your execution until the user responds. You will not be able to go back and fourth with the user, so ensure the help request contains all of the necessary information and context the user might need to respond to your request.",
};
