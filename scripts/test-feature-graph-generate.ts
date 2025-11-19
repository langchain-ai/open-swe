import { Client } from "@langchain/langgraph-sdk";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import { createLogger, LogLevel } from "@openswe/shared/logger";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";

const logger = createLogger(LogLevel.INFO, "FeatureGraphGenerateTest");

type ParsedArgs = {
  threadId?: string;
  prompt?: string;
  webUrl?: string;
  langgraphApiUrl?: string;
  skipUpstream?: boolean;
};

type ParsedResponse = {
  ok: boolean;
  status: number;
  payload: unknown;
  rawBody: string;
  reason?: string;
};

function readArgs(argv: string[]): ParsedArgs {
  const result: ParsedArgs = {};
  for (let index = 0; index < argv.length; index += 1) {
    const current = argv[index];
    const next = argv[index + 1];
    switch (current) {
      case "--thread-id":
      case "-t":
        result.threadId = next;
        index += 1;
        break;
      case "--prompt":
      case "-p":
        result.prompt = next;
        index += 1;
        break;
      case "--web-url":
      case "-w":
        result.webUrl = next;
        index += 1;
        break;
      case "--langgraph-api":
      case "-l":
        result.langgraphApiUrl = next;
        index += 1;
        break;
      case "--skip-upstream":
        result.skipUpstream = true;
        break;
      default:
        break;
    }
  }
  return result;
}

function parseResponse(responseText: string): ParsedResponse {
  let payload: unknown = null;
  try {
    payload = responseText ? JSON.parse(responseText) : null;
  } catch (error) {
    logger.warn("Response body was not JSON", { error });
  }

  return {
    ok: false,
    status: 0,
    payload,
    rawBody: responseText,
  };
}

async function readJsonResponse(label: string, response: Response): Promise<ParsedResponse> {
  const bodyText = await response.text();
  const parsed = parseResponse(bodyText);
  const payload = parsed.payload as { error?: string; message?: string } | null;
  const errorMessage = payload?.error ?? payload?.message ?? response.statusText;

  if (!response.ok) {
    logger.error(`${label} returned error`, {
      status: response.status,
      error: errorMessage,
      rawBody: parsed.rawBody,
    });
  } else {
    logger.info(`${label} succeeded`, {
      status: response.status,
      payload,
    });
  }

  return {
    ...parsed,
    ok: response.ok,
    status: response.status,
    reason: errorMessage || undefined,
  };
}

function resolveWebApiUrl(argUrl?: string): string {
  return argUrl ?? process.env.WEB_APP_URL ?? "http://localhost:3000";
}

function resolveLangGraphApiUrl(argUrl?: string): string {
  return (
    argUrl ??
    process.env.LANGGRAPH_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:2024"
  );
}

async function fetchManagerState(
  client: Client,
  threadId: string,
): Promise<ManagerGraphState | null> {
  try {
    const state = await client.threads.getState<ManagerGraphState>(threadId);
    if (!state?.values) {
      logger.error("Manager state had no values", { threadId });
      return null;
    }

    logger.info("Retrieved manager state", {
      workspaceAbsPath: state.values.workspaceAbsPath,
      workspacePath: state.values.workspacePath,
    });
    return state;
  } catch (error) {
    logger.error("Failed to load manager state", { error });
    return null;
  }
}

async function requestNextApi({
  webUrl,
  threadId,
  prompt,
}: {
  webUrl: string;
  threadId: string;
  prompt: string;
}): Promise<ParsedResponse> {
  try {
    const response = await fetch(`${webUrl}/api/feature-graph/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, prompt }),
    });

    return readJsonResponse("Next.js route /api/feature-graph/generate", response);
  } catch (error) {
    logger.error("Failed to reach Next.js route", { error });
    return { ok: false, status: 0, payload: null, rawBody: "", reason: String(error) };
  }
}

async function requestUpstream({
  langgraphApiUrl,
  workspaceAbsPath,
  prompt,
  configurable,
  localMode,
}: {
  langgraphApiUrl: string;
  workspaceAbsPath: string;
  prompt: string;
  configurable?: Record<string, unknown>;
  localMode: boolean;
}): Promise<ParsedResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (localMode) {
    headers[LOCAL_MODE_HEADER] = "true";
  }

  try {
    const response = await fetch(`${langgraphApiUrl}/feature-graph/generate`, {
      method: "POST",
      headers,
      body: JSON.stringify({ workspaceAbsPath, prompt, configurable }),
    });

    return readJsonResponse("Upstream /feature-graph/generate", response);
  } catch (error) {
    logger.error("Failed to reach upstream route", { error });
    return { ok: false, status: 0, payload: null, rawBody: "", reason: String(error) };
  }
}

function buildDiagnosticNotes({
  nextResponse,
  upstreamResponse,
  workspaceAbsPath,
  managerState,
  configurableFields,
  skippedUpstream,
}: {
  nextResponse: ParsedResponse;
  upstreamResponse?: ParsedResponse;
  workspaceAbsPath?: string;
  managerState: ManagerGraphState | null;
  configurableFields: Record<string, unknown>;
  skippedUpstream: boolean;
}): string[] {
  const notes: string[] = [];

  if (!managerState) {
    notes.push(
      "Manager state could not be loaded. Verify the thread ID exists and the LangGraph service is reachable.",
    );
  } else {
    const values = managerState.values ?? {};
    if (!workspaceAbsPath) {
      notes.push(
        "Manager state was loaded but did not expose a workspace path. Inspect manager state values for workspace path issues.",
      );
    }
    if (!values.graphStateMessageId) {
      notes.push("Manager state is missing graphStateMessageId; upstream may reject missing context.");
    }
    if (Object.keys(configurableFields).length === 0) {
      notes.push(
        "No configurable fields detected; if the graph expects configurables, verify manager metadata.configurable is populated.",
      );
    }
  }

  const describeResponse = (label: string, response: ParsedResponse): string => {
    if (response.ok) {
      return `${label} succeeded with status ${response.status}.`;
    }
    if (response.status === 0) {
      return `${label} was unreachable. Check network, service URL, and authentication headers.`;
    }
    const reason = response.reason ?? "unknown reason";
    const payloadSummary = response.rawBody ? ` Body: ${response.rawBody}` : "";
    return `${label} failed with status ${response.status} because: ${reason}.${payloadSummary}`;
  };

  if (!nextResponse.ok) {
    notes.push(describeResponse("Next.js route /api/feature-graph/generate", nextResponse));
  }

  if (upstreamResponse) {
    if (!upstreamResponse.ok) {
      notes.push(describeResponse("Upstream /feature-graph/generate", upstreamResponse));
    }
    if (nextResponse.ok && !upstreamResponse.ok) {
      notes.push(
        "Next.js route succeeded while upstream failed; the proxy layer may be configured correctly but LangGraph rejected the request.",
      );
    }
    if (!nextResponse.ok && upstreamResponse.ok) {
      notes.push(
        "Upstream succeeded while Next.js route failed; inspect Next.js API handler logic or environment variables passed to the handler.",
      );
    }
  } else if (skippedUpstream) {
    notes.push("Upstream call was skipped; rerun without --skip-upstream to compare responses.");
  }

  return notes;
}

async function main() {
  const args = readArgs(process.argv.slice(2));
  const threadId = args.threadId ?? process.env.FEATURE_GRAPH_THREAD_ID;
  const prompt = args.prompt ?? process.env.FEATURE_GRAPH_PROMPT;

  if (!threadId || !prompt) {
    logger.error(
      "Usage: yarn tsx scripts/test-feature-graph-generate.ts --thread-id <id> --prompt \"<prompt>\" [--web-url <url>] [--langgraph-api <url>] [--skip-upstream]",
    );
    process.exit(1);
  }

  const webUrl = resolveWebApiUrl(args.webUrl);
  const langgraphApiUrl = resolveLangGraphApiUrl(args.langgraphApiUrl);
  const localMode = process.env.OPEN_SWE_LOCAL_MODE === "true";

  logger.info("Starting feature graph generation check", {
    threadId,
    promptLength: prompt.length,
    webUrl,
    langgraphApiUrl,
    localMode,
  });

  const client = new Client({
    apiUrl: langgraphApiUrl,
    defaultHeaders: localMode ? { [LOCAL_MODE_HEADER]: "true" } : undefined,
  });

  const managerState = await fetchManagerState(client, threadId);
  const configurableFields =
    getCustomConfigurableFields({
      configurable: managerState?.metadata?.configurable as GraphConfig["configurable"],
    } as GraphConfig) ?? {};

  const workspaceAbsPath =
    managerState?.values?.workspaceAbsPath ?? managerState?.values?.workspacePath;

  if (!workspaceAbsPath) {
    logger.error("Manager state did not include a workspace path", { threadId });
  }

  const nextResponse = await requestNextApi({ webUrl, threadId, prompt });

  let upstreamResponse: ParsedResponse | undefined;
  if (!args.skipUpstream && workspaceAbsPath) {
    upstreamResponse = await requestUpstream({
      langgraphApiUrl,
      workspaceAbsPath,
      prompt,
      configurable:
        Object.keys(configurableFields).length > 0 ? configurableFields : undefined,
      localMode,
    });
  } else if (args.skipUpstream) {
    logger.info("Skipping upstream /feature-graph/generate call");
  }

  const diagnosticNotes = buildDiagnosticNotes({
    nextResponse,
    upstreamResponse,
    workspaceAbsPath,
    managerState,
    configurableFields,
    skippedUpstream: Boolean(args.skipUpstream),
  });

  if (diagnosticNotes.length > 0) {
    logger.info("Failure reasoning", { notes: diagnosticNotes });
  } else {
    logger.info("No failure indicators detected.");
  }
}

main().catch((error) => {
  logger.error("Feature graph test script failed", { error });
  process.exit(1);
});
