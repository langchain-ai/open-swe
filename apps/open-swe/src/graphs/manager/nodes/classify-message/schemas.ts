import { z } from "zod";

export const BASE_CLASSIFICATION_SCHEMA = z.object({
  response: z
    .string()
    .describe(
      "The response to send to the user. This should be clear, concise, and include any additional context the user may need to know about how/why you're handling their new message.",
    ),
  route: z
    .enum(["no_op"])
    .describe("The route to take to handle the user's new message."),
});

export function createClassificationSchema(inputs: {
  programmerRunning: boolean;
  showCreateIssueOption: boolean;
}) {
  const { programmerRunning, showCreateIssueOption } = inputs;

  const enumOptions = [
    ...(programmerRunning ? ["code"] : ["plan"]),
    ...(showCreateIssueOption ? ["create_new_issue"] : []),
  ];
  const schema = BASE_CLASSIFICATION_SCHEMA.extend({
    route: z
      .enum(["no_op", ...enumOptions])
      .describe("The route to take to handle the user's new message."),
  });

  return schema;
}
