// Add this import at the top
import { OpenSWEInput } from "./open-swe-types.js";
import { TargetRepository } from "@open-swe/shared/open-swe/types";
import { HumanMessage } from "@langchain/core/messages";
import { Octokit } from "@octokit/rest";
import { ManagerGraphUpdate } from "@open-swe/shared/open-swe/manager/types";


async function getRepoReadmeContents(
  targetRepository: TargetRepository,
): Promise<string> {
  if (!process.env.GITHUB_PAT) {
    throw new Error("GITHUB_PAT environment variable missing.");
  }
  const octokit = new Octokit({
    auth: process.env.GITHUB_PAT,
  });
  const { data } = await octokit.repos.getReadme({
    owner: targetRepository.owner,
    repo: targetRepository.repo,
  });
  return Buffer.from(data.content, "base64").toString("utf-8");
}


export async function formatInputs(
  inputs: OpenSWEInput,
): Promise<ManagerGraphUpdate> {
  const targetRepository: TargetRepository = {
    owner: inputs.repo.split("/")[0],
    repo: inputs.repo.split("/")[1],
    baseCommit: "HEAD", // Since we don't have base_commit, use HEAD
  };
  
  const readmeContents = await getRepoReadmeContents(targetRepository);
  
  // Simplified prompt for OpenSWE (no hints, no complex SWE-Bench context)
  const SIMPLE_PROMPT_TEMPLATE = `

<request>
{USER_REQUEST}
</request>

<codebase-readme>
{CODEBASE_README}
</codebase-readme>
`;

  const userMessageContent = SIMPLE_PROMPT_TEMPLATE
    .replace("{REPO}", inputs.repo)
    .replace("{USER_REQUEST}", inputs.user_input)
    .replace("{CODEBASE_README}", readmeContents);

  const userMessage = new HumanMessage(userMessageContent);
  return {
    messages: [userMessage],
    targetRepository,
    autoAcceptPlan: true,
  };
}