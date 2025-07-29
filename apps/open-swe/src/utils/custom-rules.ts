import { CustomRules } from "@open-swe/shared/open-swe/types";
import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "./logger.js";
import { getSandboxErrorFields } from "./sandbox-error-fields.js";

const logger = createLogger(LogLevel.INFO, "CustomRules");

const GENERAL_RULES_OPEN_TAG = "<general_rules>";
const GENERAL_RULES_CLOSE_TAG = "</general_rules>";
const REPOSITORY_STRUCTURE_OPEN_TAG = "<repository_structure>";
const REPOSITORY_STRUCTURE_CLOSE_TAG = "</repository_structure>";
const DEPENDENCIES_AND_INSTALLATION_OPEN_TAG =
  "<dependencies_and_installation>";
const DEPENDENCIES_AND_INSTALLATION_CLOSE_TAG =
  "</dependencies_and_installation>";
const TESTING_INSTRUCTIONS_OPEN_TAG = "<testing_instructions>";
const TESTING_INSTRUCTIONS_CLOSE_TAG = "</testing_instructions>";
const PULL_REQUEST_FORMATTING_OPEN_TAG = "<pull_request_formatting>";
const PULL_REQUEST_FORMATTING_CLOSE_TAG = "</pull_request_formatting>";
const ALL_TAGS = [
  GENERAL_RULES_OPEN_TAG,
  GENERAL_RULES_CLOSE_TAG,
  REPOSITORY_STRUCTURE_OPEN_TAG,
  REPOSITORY_STRUCTURE_CLOSE_TAG,
  DEPENDENCIES_AND_INSTALLATION_OPEN_TAG,
  DEPENDENCIES_AND_INSTALLATION_CLOSE_TAG,
  TESTING_INSTRUCTIONS_OPEN_TAG,
  TESTING_INSTRUCTIONS_CLOSE_TAG,
  PULL_REQUEST_FORMATTING_OPEN_TAG,
  PULL_REQUEST_FORMATTING_CLOSE_TAG,
];

export function parseCustomRulesFromString(
  contents: string,
): CustomRules | undefined {
  if (ALL_TAGS.every((tag) => !contents.includes(tag))) {
    // Text file has no custom rules. Return all as general rules
    return {
      generalRules: contents,
    };
  }
  let generalRules = "";
  let repositoryStructure = "";
  let dependenciesAndInstallation = "";
  let testingInstructions = "";
  let pullRequestFormatting = "";

  if (
    contents.includes(GENERAL_RULES_OPEN_TAG) &&
    contents.includes(GENERAL_RULES_CLOSE_TAG)
  ) {
    generalRules = contents.substring(
      contents.indexOf(GENERAL_RULES_OPEN_TAG) + GENERAL_RULES_OPEN_TAG.length,
      contents.indexOf(GENERAL_RULES_CLOSE_TAG),
    );
  }
  if (
    contents.includes(REPOSITORY_STRUCTURE_OPEN_TAG) &&
    contents.includes(REPOSITORY_STRUCTURE_CLOSE_TAG)
  ) {
    repositoryStructure = contents.substring(
      contents.indexOf(REPOSITORY_STRUCTURE_OPEN_TAG) +
        REPOSITORY_STRUCTURE_OPEN_TAG.length,
      contents.indexOf(REPOSITORY_STRUCTURE_CLOSE_TAG),
    );
  }
  if (
    contents.includes(DEPENDENCIES_AND_INSTALLATION_OPEN_TAG) &&
    contents.includes(DEPENDENCIES_AND_INSTALLATION_CLOSE_TAG)
  ) {
    dependenciesAndInstallation = contents.substring(
      contents.indexOf(DEPENDENCIES_AND_INSTALLATION_OPEN_TAG) +
        DEPENDENCIES_AND_INSTALLATION_OPEN_TAG.length,
      contents.indexOf(DEPENDENCIES_AND_INSTALLATION_CLOSE_TAG),
    );
  }
  if (
    contents.includes(TESTING_INSTRUCTIONS_OPEN_TAG) &&
    contents.includes(TESTING_INSTRUCTIONS_CLOSE_TAG)
  ) {
    testingInstructions = contents.substring(
      contents.indexOf(TESTING_INSTRUCTIONS_OPEN_TAG) +
        TESTING_INSTRUCTIONS_OPEN_TAG.length,
      contents.indexOf(TESTING_INSTRUCTIONS_CLOSE_TAG),
    );
  }
  if (
    contents.includes(PULL_REQUEST_FORMATTING_OPEN_TAG) &&
    contents.includes(PULL_REQUEST_FORMATTING_CLOSE_TAG)
  ) {
    pullRequestFormatting = contents.substring(
      contents.indexOf(PULL_REQUEST_FORMATTING_OPEN_TAG) +
        PULL_REQUEST_FORMATTING_OPEN_TAG.length,
      contents.indexOf(PULL_REQUEST_FORMATTING_CLOSE_TAG),
    );
  }

  if (
    !generalRules &&
    !repositoryStructure &&
    !dependenciesAndInstallation &&
    !testingInstructions &&
    !pullRequestFormatting
  ) {
    return undefined;
  }

  return {
    generalRules,
    repositoryStructure,
    dependenciesAndInstallation,
    testingInstructions,
    pullRequestFormatting,
  };
}

export async function getCustomRules(
  sandbox: Sandbox,
  rootDir: string,
): Promise<CustomRules | undefined> {
  try {
    const catAgentsMdFileCommand = ["cat", "AGENTS.md"];
    const agentsMdRes = await sandbox.process.executeCommand(
      catAgentsMdFileCommand.join(" "),
      rootDir,
    );
    if (agentsMdRes.exitCode === 0 && agentsMdRes.result?.length > 0) {
      return parseCustomRulesFromString(agentsMdRes.result);
    }

    const catAgentMdFileCommand = ["cat", "AGENT.md"];
    const catClaudeMdFileCommand = ["cat", "CLAUDE.md"];
    const catCursorMdFileCommand = ["cat", "CURSOR.md"];
    const [agentMdRes, claudeMdRes, cursorMdRes] = await Promise.all([
      sandbox.process.executeCommand(catAgentMdFileCommand.join(" "), rootDir),
      sandbox.process.executeCommand(catClaudeMdFileCommand.join(" "), rootDir),
      sandbox.process.executeCommand(catCursorMdFileCommand.join(" "), rootDir),
    ]);
    if (agentMdRes.exitCode === 0 && agentMdRes.result?.length > 0) {
      return parseCustomRulesFromString(agentMdRes.result);
    }
    if (claudeMdRes.exitCode === 0 && claudeMdRes.result?.length > 0) {
      return parseCustomRulesFromString(claudeMdRes.result);
    }
    if (cursorMdRes.exitCode === 0 && cursorMdRes.result?.length > 0) {
      return parseCustomRulesFromString(cursorMdRes.result);
    }
  } catch (error) {
    const sandboxErrorFields = getSandboxErrorFields(error);
    logger.error("Failed to get custom rules", {
      ...(sandboxErrorFields ? { ...sandboxErrorFields } : { error }),
    });
  }

  return undefined;
}

export const CUSTOM_RULES_PROMPT = `<custom_rules>
The following are custom rules provided by the user.
{EXTRA_CONTEXT}
{GENERAL_RULES}
{REPOSITORY_STRUCTURE}
{TESTING_INSTRUCTIONS}
{DEPENDENCIES_AND_INSTALLATION}
</custom_rules>`;

export function formatCustomRulesPrompt(
  customRules?: CustomRules,
  extraContextStr?: string,
): string {
  if (!customRules) return "";
  return CUSTOM_RULES_PROMPT.replace(
    "{EXTRA_CONTEXT}",
    extraContextStr ? extraContextStr : "",
  )
    .replace(
      "{GENERAL_RULES}",
      customRules.generalRules
        ? `<general_rules>\n${customRules.generalRules}\n</general_rules>`
        : "",
    )
    .replace(
      "{REPOSITORY_STRUCTURE}",
      customRules.repositoryStructure
        ? `<repository_structure>\n${customRules.repositoryStructure}\n</repository_structure>`
        : "",
    )
    .replace(
      "{TESTING_INSTRUCTIONS}",
      customRules.testingInstructions
        ? `<testing_instructions>\n${customRules.testingInstructions}\n</testing_instructions>`
        : "",
    )
    .replace(
      "{DEPENDENCIES_AND_INSTALLATION}",
      customRules.dependenciesAndInstallation
        ? `<dependencies_and_installation>\n${customRules.dependenciesAndInstallation}\n</dependencies_and_installation>`
        : "",
    );
}
