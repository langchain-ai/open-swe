import { PRData } from "./types.js";

export function createPRFixPrompt(prData: PRData): string {
  return `
  <request>

  Your job is to fix the issues in PR with title: ${prData.title}

  The PR has the following description:
  ${prData.body}

  The PR has the following test files:
  ${prData.testFiles.join("\n")}
  `;
}