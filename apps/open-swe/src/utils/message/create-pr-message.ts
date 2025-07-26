import { v4 as uuidv4 } from "uuid";
import { AIMessage, BaseMessage, ToolMessage } from "@langchain/core/messages";
import { createOpenPrToolFields } from "@open-swe/shared/open-swe/tools";
import { z } from "zod";
import { TargetRepository } from "@open-swe/shared/open-swe/types";

function constructPullRequestUrl(
  targetRepository: TargetRepository,
  number: number,
) {
  return `https://github.com/${targetRepository.owner}/${targetRepository.repo}/pull/${number}`;
}

export function createPullRequestToolCallMessage(
  targetRepository: TargetRepository,
  number: number,
  isDraft?: boolean,
): BaseMessage[] {
  const openPrTool = createOpenPrToolFields();
  const openPrToolArgs: z.infer<typeof openPrTool.schema> = {
    title: "",
    body: "",
  };
  const toolCallId = uuidv4();
  return [
    new AIMessage({
      id: uuidv4(),
      content: "",
      tool_calls: [
        {
          name: openPrTool.name,
          args: openPrToolArgs,
          id: toolCallId,
        },
      ],
    }),
    new ToolMessage({
      id: uuidv4(),
      tool_call_id: toolCallId,
      content: `${isDraft ? "Opened draft" : "Opened"} pull request: ${constructPullRequestUrl(targetRepository, number)}`,
      name: openPrTool.name,
      status: "success",
    }),
  ];
}
