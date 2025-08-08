import { CustomRules } from "@open-swe/shared/open-swe/types";
import { Sandbox } from "@daytonaio/sdk";
import { createLogger, LogLevel } from "./logger.js";
import { getSandboxErrorFields } from "./sandbox-error-fields.js";
import {
  isLocalMode,
  getLocalWorkingDirectory,
} from "@open-swe/shared/open-swe/local-mode";
import { promises as fs } from "fs";
import { join } from "path";
import { GraphConfig } from "@open-swe/shared/open-swe/types";
import { createShellExecutor } from "./shell-executor/shell-executor.js";

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
  config: GraphConfig,
): Promise<CustomRules | undefined> {
  try {
    if (isLocalMode(config)) {
      return getCustomRulesLocal(rootDir);
    }

    const executor = createShellExecutor(config);

    const catAgentsMdFileCommand = ["cat", "AGENTS.md"];
    const agentsMdRes = await executor.executeCommand({
      command: catAgentsMdFileCommand.join(" "),
      workdir: rootDir,
      sandbox,
    });
    if (agentsMdRes.exitCode === 0 && agentsMdRes.result?.length > 0) {
      return parseCustomRulesFromString(agentsMdRes.result);
    }

    const catAgentMdFileCommand = ["cat", "AGENT.md"];
    const catClaudeMdFileCommand = ["cat", "CLAUDE.md"];
    const catCursorMdFileCommand = ["cat", "CURSOR.md"];
    const [agentMdRes, claudeMdRes, cursorMdRes] = await Promise.all([
      executor.executeCommand({
        command: catAgentMdFileCommand.join(" "),
        workdir: rootDir,
        sandbox,
      }),
      executor.executeCommand({
        command: catClaudeMdFileCommand.join(" "),
        workdir: rootDir,
        sandbox,
      }),
      executor.executeCommand({
        command: catCursorMdFileCommand.join(" "),
        workdir: rootDir,
        sandbox,
      }),
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

/**
 * Local version of getCustomRules using Node.js fs
 */
async function getCustomRulesLocal(
  rootDir: string,
): Promise<CustomRules | undefined> {
  try {
    const workingDirectory = rootDir || getLocalWorkingDirectory();

    // Try to read AGENTS.md first
    try {
      const agentsMdPath = join(workingDirectory, "AGENTS.md");
      const agentsMdContent = await fs.readFile(agentsMdPath, "utf-8");
      if (agentsMdContent && agentsMdContent.length > 0) {
        return parseCustomRulesFromString(agentsMdContent);
      }
    } catch (error) {
      logger.debug("AGENTS.md not found, trying other files", { error });
    }

    // Try to read AGENT.md, CLAUDE.md, CURSOR.md
    const filesToTry = ["AGENT.md", "CLAUDE.md", "CURSOR.md"];

    for (const fileName of filesToTry) {
      try {
        const filePath = join(workingDirectory, fileName);
        const content = await fs.readFile(filePath, "utf-8");
        if (content && content.length > 0) {
          return parseCustomRulesFromString(content);
        }
      } catch (error) {
        // File doesn't exist, continue to next file
        logger.error(`Failed to read ${fileName}`, { error });
      }
    }
  } catch (error) {
    logger.error("Failed to get custom rules in local mode", { error });
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
