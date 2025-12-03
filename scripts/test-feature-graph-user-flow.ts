import path from "node:path";

import { Client } from "@langchain/langgraph-sdk";
import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import { loadFeatureGraph } from "@openswe/shared/feature-graph/loader";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import { createLogger, LogLevel } from "@openswe/shared/logger";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";

const logger = createLogger(LogLevel.INFO, "FeatureGraphUserFlowTest");

type ParsedArgs = {
  threadId?: string;
  prompt?: string;
  webUrl?: string;
  langgraphApiUrl?: string;
  localMode?: boolean;
};

type ParsedResponse = {
  ok: boolean;
  status: number;
  payload: unknown;
  rawBody: string;
  reason?: string;
};

type GraphFileResult = {
  graph?: FeatureGraph;
  nodeCount?: number;
  edgeCount?: number;
  error?: string;
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
      case "--local-mode":
        result.localMode = true;
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

async function requestNextFeatureGraph({
  webUrl,
  threadId,
  prompt,
}: {
  webUrl: string;
  threadId: string;
  prompt: string;
}): Promise<ParsedResponse> {
  const requestBody = JSON.stringify({ thread_id: threadId, prompt });
  logger.info("Dispatching request to Next.js feature-graph generate route", {
    endpoint: `${webUrl}/api/feature-graph/generate`,
    requestBody,
  });

  try {
    const response = await fetch(`${webUrl}/api/feature-graph/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: requestBody,
    });

    return readJsonResponse("Next.js route /api/feature-graph/generate", response);
  } catch (error) {
    logger.error("Failed to reach Next.js route", { error });
    return { ok: false, status: 0, payload: null, rawBody: "", reason: String(error) };
  }
}

async function loadGraphFile(workspaceAbsPath: string): Promise<GraphFileResult> {
  const graphPath = path.join(workspaceAbsPath, "features/graph/graph.yaml");
  try {
    const data = await loadFeatureGraph(graphPath);
    const graph = new FeatureGraph(data);
    const nodeCount = graph.listFeatures().length;
    const edgeCount = graph.listEdges().length;
    logger.info("Loaded persisted feature graph", {
      graphPath,
      nodeCount,
      edgeCount,
    });
    return { graph, nodeCount, edgeCount };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    logger.error("Failed to load feature graph from workspace", { graphPath, error: message });
    return { error: message };
  }
}

function summarizeManagerState(managerState: ManagerGraphState | null): {
  featureGraphPresent: boolean;
  activeFeatureIdsPresent: boolean;
  featureCount?: number;
  activeCount?: number;
  workspaceAbsPath?: string;
  missingFields: string[];
} {
  if (!managerState?.values) {
    return {
      featureGraphPresent: false,
      activeFeatureIdsPresent: false,
      missingFields: ["values"],
    };
  }

  const values = managerState.values;
  const missingFields: string[] = [];
  const featureGraph = values.featureGraph;
  const activeFeatureIds = values.activeFeatureIds;

  if (!featureGraph) {
    missingFields.push("featureGraph");
  }

  if (!activeFeatureIds) {
    missingFields.push("activeFeatureIds");
  }

  const featureGraphPresent = Boolean(featureGraph);
  const activeFeatureIdsPresent = Boolean(activeFeatureIds);
  const featureCount = featureGraphPresent ? featureGraph.listFeatures().length : undefined;
  const activeCount = Array.isArray(activeFeatureIds) ? activeFeatureIds.length : undefined;

  if (featureGraphPresent || activeFeatureIdsPresent) {
    logger.info("Manager state contains feature graph data", {
      featureGraphPresent,
      featureCount,
      activeFeatureIdsPresent,
      activeCount,
    });
  } else {
    logger.error("Manager state missing feature graph data", { missingFields });
  }

  return {
    featureGraphPresent,
    activeFeatureIdsPresent,
    featureCount,
    activeCount,
    workspaceAbsPath: values.workspaceAbsPath ?? values.workspacePath,
    missingFields,
  };
}

function buildDiagnosticSummary({
  prompt,
  nextResponse,
  managerSummary,
  graphResult,
}: {
  prompt: string;
  nextResponse: ParsedResponse;
  managerSummary: ReturnType<typeof summarizeManagerState>;
  graphResult?: GraphFileResult;
}): string {
  const notes: string[] = [];

  notes.push(`Prompt length: ${prompt.length}`);
  notes.push(`Next.js response: ${nextResponse.status} (ok=${nextResponse.ok}). Raw body: ${nextResponse.rawBody}`);

  if (managerSummary.featureGraphPresent) {
    notes.push(
      `Manager state includes featureGraph with ${managerSummary.featureCount ?? 0} nodes and ${
        managerSummary.activeCount ?? 0
      } active IDs.`,
    );
  } else {
    notes.push(`Manager state missing fields: ${managerSummary.missingFields.join(", ") || "none"}`);
  }

  if (graphResult) {
    if (graphResult.graph) {
      notes.push(
        `Workspace graph file loaded with ${graphResult.nodeCount ?? 0} nodes and ${graphResult.edgeCount ?? 0} edges.`,
      );
    } else {
      notes.push(`Workspace graph load failed: ${graphResult.error ?? "unknown error"}`);
    }
  }

  return notes.join(" ");
}

async function main() {
  const args = readArgs(process.argv.slice(2));
  const threadId = args.threadId ?? process.env.FEATURE_GRAPH_THREAD_ID;
  const prompt =
    args.prompt ??
    process.env.FEATURE_GRAPH_PROMPT ??
    "Design a concise feature plan that outlines user stories and engineering tasks for the next milestone.";

  if (!threadId) {
    logger.error(
      "Usage: yarn tsx scripts/test-feature-graph-user-flow.ts --thread-id <id> [--prompt \"<prompt>\"] [--web-url <url>] [--langgraph-api <url>] [--local-mode]",
    );
    process.exit(1);
  }

  const webUrl = resolveWebApiUrl(args.webUrl);
  const langgraphApiUrl = resolveLangGraphApiUrl(args.langgraphApiUrl);
  const localMode = args.localMode ?? process.env.OPEN_SWE_LOCAL_MODE === "true";

  logger.info("Starting feature graph user flow check", {
    threadId,
    promptLength: prompt.length,
    webUrl,
    langgraphApiUrl,
    localMode,
  });

  const nextResponse = await requestNextFeatureGraph({ webUrl, threadId, prompt });

  const client = new Client({
    apiUrl: langgraphApiUrl,
    defaultHeaders: localMode ? { [LOCAL_MODE_HEADER]: "true" } : undefined,
  });
  const managerState = await fetchManagerState(client, threadId);
  const managerSummary = summarizeManagerState(managerState);

  let graphResult: GraphFileResult | undefined;
  if (managerSummary.workspaceAbsPath) {
    graphResult = await loadGraphFile(managerSummary.workspaceAbsPath);
  } else {
    logger.warn("Workspace path unavailable; skipping graph.yaml validation");
  }

  const diagnosticSummary = buildDiagnosticSummary({
    prompt,
    nextResponse,
    managerSummary,
    graphResult,
  });

  logger.info("Diagnostic summary", { summary: diagnosticSummary });
}

main().catch((error) => {
  logger.error("Feature graph user flow script failed", { error });
  process.exit(1);
});
