import { Sandbox } from "@e2b/code-interpreter";
import {
  GraphState,
  GraphConfig,
  GraphUpdate,
  TargetRepository,
} from "../types.js";
import { TIMEOUT_EXTENSION_OPT } from "../constants.js";

const JS_SANDBOX_TEMPLATE_ID = "eh0860emqx28qyxmbctu";

async function cloneRepo(sandbox: Sandbox, targetRepository: TargetRepository) {
  if (!process.env.GITHUB_PAT) {
    throw new Error("GITHUB_PAT environment variable not set.");
  }

  const gitCloneCommand = ["git", "clone"];

  const repoUrlWithToken = `https://${process.env.GITHUB_PAT}@github.com/${targetRepository.owner}/${targetRepository.repo}.git`;

  if (targetRepository.branch) {
    gitCloneCommand.push("-b", targetRepository.branch, repoUrlWithToken);
  } else {
    gitCloneCommand.push(repoUrlWithToken);
  }

  return await sandbox.commands.run(
    gitCloneCommand.join(" "),
    TIMEOUT_EXTENSION_OPT,
  );
}

/**
 * Initializes the session. This ensures there's an active VM session, and that
 * the proper credentials are provided for taking actions on GitHub.
 * It also clones the repository the user has specified to be used, and an optional
 * branch.
 */
export async function initialize(
  _state: GraphState,
  config: GraphConfig,
): Promise<GraphUpdate> {
  if (!config.configurable) {
    throw new Error("Configuration object not found.");
  }
  const { sandbox_session_id, target_repository, sandbox_language } =
    config.configurable;
  if (sandbox_session_id) {
    try {
      // Resume the sandbox if the session ID is in the config.
      await Sandbox.resume(sandbox_session_id, TIMEOUT_EXTENSION_OPT);
      return {};
    } catch (e) {
      // Error thrown, log it and continue. Will create a new sandbox session since the resumption failed.
      console.error("Failed to get sandbox session.", e);
    }
  }

  if (!sandbox_language || !target_repository) {
    throw new Error(
      "Missing required configuration. Please provide a sandbox language and git repository URL.",
    );
  }

  if (sandbox_language === "js") {
    const sandbox = await Sandbox.create(
      JS_SANDBOX_TEMPLATE_ID,
      TIMEOUT_EXTENSION_OPT,
    );
    config.configurable.sandbox_session_id = sandbox.sandboxId;

    const res = await cloneRepo(sandbox, target_repository);
    if (res.error) {
      // TODO: This should probably be an interrupt.
      throw new Error(`Failed to clone repository.\n${res.error}`);
    }
    return {};
  }

  if (sandbox_language === "python") {
    throw new Error("Python sandbox not implemented yet.");
  }

  throw new Error("Unsupported sandbox language: " + sandbox_language);
}
