import { OpenSWEInput } from "./open-swe-types.js";
import { TargetRepository } from "@openswe/shared/open-swe/types";
import { HumanMessage } from "@langchain/core/messages";
import { ManagerGraphUpdate } from "@openswe/shared/open-swe/manager/types";

async function getRepoReadmeContents(
  _targetRepository: TargetRepository,
): Promise<string> {
  return "";
}

export async function formatInputs(
  inputs: OpenSWEInput,
): Promise<ManagerGraphUpdate> {
  const targetRepository: TargetRepository = {
    owner: inputs.repo.split("/")[0],
    repo: inputs.repo.split("/")[1],
    branch: inputs.branch,
  };

  const readmeContents = await getRepoReadmeContents(targetRepository);

  const SIMPLE_PROMPT_TEMPLATE = `<request>
{USER_REQUEST}
</request>

<codebase-readme>
{CODEBASE_README}
</codebase-readme>`;

  const userMessageContent = SIMPLE_PROMPT_TEMPLATE.replace(
    "{REPO}",
    inputs.repo,
  )
    .replace("{USER_REQUEST}", inputs.user_input)
    .replace("{CODEBASE_README}", readmeContents);

  const userMessage = new HumanMessage(userMessageContent);
  return {
    messages: [userMessage],
    targetRepository,
    autoAcceptPlan: true,
  };
}
