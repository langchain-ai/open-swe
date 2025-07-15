import { BaseMessage, isAIMessage } from "@langchain/core/messages";
import { createCodeReviewMarkTaskNotCompleteFields } from "@open-swe/shared/open-swe/tools";
import { z } from "zod";

export function getCodeReviewFields(
  messages: BaseMessage[],
): { review: string; newActions: string[] } | null {
  const codeReviewToolFields = createCodeReviewMarkTaskNotCompleteFields();
  const codeReviewMessage = messages
    .filter(isAIMessage)
    .findLast(
      (m) =>
        m.tool_calls?.length &&
        m.tool_calls.some((tc) => tc.name === codeReviewToolFields.name),
    );
  const codeReviewToolCall = codeReviewMessage?.tool_calls?.find(
    (tc) => tc.name === codeReviewToolFields.name,
  );
  if (!codeReviewMessage || !codeReviewToolCall) return null;
  const codeReviewArgs = codeReviewToolCall.args as z.infer<
    typeof codeReviewToolFields.schema
  >;
  if (!codeReviewArgs.review || !codeReviewArgs.additional_actions?.length)
    return null;

  return {
    review: codeReviewArgs.review,
    newActions: codeReviewArgs.additional_actions,
  };
}

export function formatCodeReviewPrompt(
  reviewPrompt: string,
  inputs: {
    review: string;
    newActions: string[];
  },
): string {
  return reviewPrompt
    .replaceAll("{CODE_REVIEW}", inputs.review)
    .replaceAll(
      "{CODE_REVIEW_ACTIONS}",
      inputs.newActions.map((a) => `* ${a}`).join("\n"),
    );
}
