// Run evals over the development SWE Bench Lite dataset

import "dotenv/config";
import { SWEBenchInput } from "./swe-bench-types.js";
import { GraphUpdate, TargetRepository } from "@open-swe/shared/open-swe/types";
import { Octokit } from "@octokit/rest";
import { HumanMessage } from "@langchain/core/messages";

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

/**
 * The following prompt template is a modified version of the prompt template used in the SWE Bench paper.
 * We've modified it because our agent acts differently than the one used in the paper, and thus some
 * of the instructions are not relevant to our agent.
 * @see https://arxiv.org/pdf/2310.06770 - Page 30, Appendix D.3 ("PROMPT TEMPLATE EXAMPLE")
 */
const PROMPT_TEMPLATE = `You've been provided with a clone of the {REPO} repository and an issue statement explaining a problem to resolve.

<issue>
{PROBLEM_STATEMENT}
</issue>
{OPTIONAL_HINTS_TEXT}
<codebase-readme>
{CODEBASE_README}
</codebase-readme>

I need you to solve the provided issue by generating and applying a patch to a single file which will resolve the issue.
You are NOT expected to write your own tests, or run tests to confirm your patch resolved the issue.
Once you've applied the patch you may finish, and I will manually review your code to ensure it resolved the issue.`;

export async function formatInputs(
  inputs: SWEBenchInput,
): Promise<GraphUpdate> {
  const targetRepository: TargetRepository = {
    owner: inputs.repo.split("/")[0],
    repo: inputs.repo.split("/")[1],
    baseCommit: inputs.base_commit,
  };
  const readmeContents = await getRepoReadmeContents(targetRepository);
  const userMessageContent = PROMPT_TEMPLATE.replace(
    "{REPO}",
    inputs.original_repo || inputs.repo,
  )
    .replace("{PROBLEM_STATEMENT}", inputs.problem_statement)
    .replace(
      "{OPTIONAL_HINTS_TEXT}",
      inputs.hints_text
        ? `\nThe following is the conversation history on the GitHub issue. You may use this as context\n<issue-comments>\n${inputs.hints_text}\n</issue-comments>\n`
        : "",
    )
    .replace("{CODEBASE_README}", readmeContents);

  const userMessage = new HumanMessage(userMessageContent);
  return {
    messages: [userMessage],
    internalMessages: [userMessage],
    targetRepository,
  };
}
