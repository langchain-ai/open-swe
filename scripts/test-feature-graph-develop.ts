/**
 * Usage:
 *   yarn tsx scripts/test-feature-graph-develop.ts --thread-id <id> [--feature-id <id>] [--repo <path>] [--web-url <url>] [--langgraph-api <url>] [--skip-upstream]
 *
 * Environment variables:
 *   FEATURE_GRAPH_THREAD_ID: fallback thread id when --thread-id is not provided
 *   FEATURE_GRAPH_FEATURE_ID: optional feature id override
 *   FEATURE_GRAPH_REPO_PATH: repository root containing features/graph/graph.yaml (defaults to current working directory)
 *   WEB_APP_URL / LANGGRAPH_API_URL / NEXT_PUBLIC_API_URL: service URLs mirroring the generate test script
 *   OPEN_SWE_LOCAL_MODE: when "true", adds the local-mode header to upstream requests
 *
 * Sample output:
 *   yarn tsx scripts/test-feature-graph-develop.ts --thread-id 123 --repo /workspace/repo
 *   [INFO] FeatureGraphDevelopTest Starting feature graph development check { threadId: "123", webUrl: "http://localhost:3000", ... }
 *   [INFO] FeatureGraphDevelopTest Selected feature from graph { featureId: "feature-1", title: "Initial wiring" }
 *   [INFO] FeatureGraphDevelopTest Retrieved manager state { workspaceAbsPath: "/workspace/repo", workspacePath: "repo" }
 *   [INFO] FeatureGraphDevelopTest Next.js route /api/feature-graph/develop succeeded { status: 200, payload: { planner_thread_id: "...", run_id: "..." } }
 *   [INFO] FeatureGraphDevelopTest Upstream /feature-graph/develop succeeded { status: 200, payload: { planner_thread_id: "...", run_id: "..." } }
 *   [INFO] FeatureGraphDevelopTest No failure indicators detected.
 */

import path from "node:path";
import { Client } from "@langchain/langgraph-sdk";
import { FeatureGraph } from "@openswe/shared/feature-graph/graph";
import { loadFeatureGraph } from "@openswe/shared/feature-graph/loader";
import type { FeatureNode } from "@openswe/shared/feature-graph/types";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import { createLogger, LogLevel } from "@openswe/shared/logger";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";

const logger = createLogger(LogLevel.INFO, "FeatureGraphDevelopTest");

type ParsedArgs = {
  threadId?: string;
  featureId?: string;
  repoPath?: string;
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

type ResponseValidation = {
  plannerThreadId?: string;
  runId?: string;
  issues: string[];
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
      case "--feature-id":
      case "-f":
        result.featureId = next;
        index += 1;
        break;
      case "--repo":
      case "-r":
        result.repoPath = next;
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

function resolveRepoPath(argRepoPath?: string): string {
  return path.resolve(argRepoPath ?? process.env.FEATURE_GRAPH_REPO_PATH ?? process.cwd());
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

async function loadGraph(repoPath: string): Promise<FeatureGraph | null> {
  const graphPath = path.join(repoPath, "features/graph/graph.yaml");
  try {
    const data = await loadFeatureGraph(graphPath);
    const graph = new FeatureGraph(data);
    logger.info("Loaded feature graph", {
      repoPath,
      graphPath,
      nodeCount: graph.listFeatures().length,
      edgeCount: graph.listEdges().length,
    });
    return graph;
  } catch (error) {
    logger.error("Failed to load feature graph", {
      graphPath,
      error: error instanceof Error ? error.message : String(error),
    });
    return null;
  }
}

function validateResponseShape(response: ParsedResponse): ResponseValidation {
  const validation: ResponseValidation = { issues: [] };
  const payload = response.payload as Record<string, unknown> | null;
  const plannerThreadId = payload?.planner_thread_id;
  const runId = payload?.run_id;

  if (typeof plannerThreadId === "string" && plannerThreadId.trim()) {
    validation.plannerThreadId = plannerThreadId;
  } else {
    validation.issues.push("planner_thread_id missing or invalid in response payload.");
  }

  if (typeof runId === "string" && runId.trim()) {
    validation.runId = runId;
  } else {
    validation.issues.push("run_id missing or invalid in response payload.");
  }

  return validation;
}

async function requestNextApi({
  webUrl,
  threadId,
  featureId,
}: {
  webUrl: string;
  threadId: string;
  featureId: string;
}): Promise<ParsedResponse> {
  try {
    const response = await fetch(`${webUrl}/api/feature-graph/develop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId, feature_id: featureId }),
    });

    return readJsonResponse("Next.js route /api/feature-graph/develop", response);
  } catch (error) {
    logger.error("Failed to reach Next.js route", { error });
    return { ok: false, status: 0, payload: null, rawBody: "", reason: String(error) };
  }
}

async function requestUpstream({
  langgraphApiUrl,
  featureId,
  threadId,
  localMode,
}: {
  langgraphApiUrl: string;
  featureId: string;
  threadId: string;
  localMode: boolean;
}): Promise<ParsedResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (localMode) {
    headers[LOCAL_MODE_HEADER] = "true";
  }

  try {
    const response = await fetch(`${langgraphApiUrl}/feature-graph/develop`, {
      method: "POST",
      headers,
      body: JSON.stringify({ thread_id: threadId, feature_id: featureId }),
    });

    return readJsonResponse("Upstream /feature-graph/develop", response);
  } catch (error) {
    logger.error("Failed to reach upstream route", { error });
    return { ok: false, status: 0, payload: null, rawBody: "", reason: String(error) };
  }
}

function selectFeature(
  graph: FeatureGraph | null,
  requestedFeatureId?: string,
): { featureId?: string; feature?: FeatureNode; notes: string[] } {
  if (!graph) {
    return { notes: ["Feature graph could not be loaded; feature selection skipped."] };
  }

  const notes: string[] = [];

  if (requestedFeatureId) {
    const matched = graph.getFeature(requestedFeatureId);
    if (!matched) {
      notes.push("Requested feature_id not found in graph; selection will use the first available feature.");
    } else {
      logger.info("Selected feature from provided feature_id", {
        featureId: matched.id,
        title: matched.title,
      });
      return { featureId: matched.id, feature: matched, notes };
    }
  }

  const firstFeature = graph.listFeatures()[0];
  if (!firstFeature) {
    notes.push("Feature graph contains no nodes; provide a feature_id or regenerate the graph.");
    return { notes };
  }

  logger.info("Selected feature from graph", {
    featureId: firstFeature.id,
    title: firstFeature.title,
  });
  return { featureId: firstFeature.id, feature: firstFeature, notes };
}

function buildDiagnosticNotes({
  graph,
  featureSelectionNotes,
  targetFeatureId,
  managerState,
  nextResponse,
  upstreamResponse,
  skippedUpstream,
  nextValidation,
  upstreamValidation,
}: {
  graph: FeatureGraph | null;
  featureSelectionNotes: string[];
  targetFeatureId?: string;
  managerState: ManagerGraphState | null;
  nextResponse: ParsedResponse;
  upstreamResponse?: ParsedResponse;
  skippedUpstream: boolean;
  nextValidation: ResponseValidation;
  upstreamValidation?: ResponseValidation;
}): string[] {
  const notes = [...featureSelectionNotes];

  if (!graph) {
    notes.push("Feature graph was not loaded; verify the repo path and graph.yaml availability.");
  }

  if (!targetFeatureId) {
    notes.push("No feature_id was determined; the develop endpoint will reject the request.");
  }

  if (!managerState) {
    notes.push("Manager state could not be loaded. Ensure the thread exists and LangGraph is reachable.");
  } else if (!managerState.values?.workspacePath) {
    notes.push("Manager state is missing workspacePath; planner run configuration may be incomplete.");
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
    notes.push(describeResponse("Next.js route /api/feature-graph/develop", nextResponse));
  }

  if (nextValidation.issues.length > 0) {
    notes.push(
      `Next.js response validation issues: ${nextValidation.issues.join(" ")}`,
    );
  }

  if (upstreamResponse) {
    if (!upstreamResponse.ok) {
      notes.push(describeResponse("Upstream /feature-graph/develop", upstreamResponse));
    }

    if (upstreamValidation && upstreamValidation.issues.length > 0) {
      notes.push(
        `Upstream response validation issues: ${upstreamValidation.issues.join(" ")}`,
      );
    }

    if (nextResponse.ok && upstreamResponse.ok) {
      if (nextResponse.status !== upstreamResponse.status) {
        notes.push("Next.js and upstream responses returned different HTTP status codes.");
      }
      if (
        nextValidation.plannerThreadId &&
        upstreamValidation?.plannerThreadId &&
        nextValidation.plannerThreadId !== upstreamValidation.plannerThreadId
      ) {
        notes.push("planner_thread_id mismatch between Next.js and upstream responses.");
      }
      if (
        nextValidation.runId &&
        upstreamValidation?.runId &&
        nextValidation.runId !== upstreamValidation.runId
      ) {
        notes.push("run_id mismatch between Next.js and upstream responses.");
      }
    } else if (nextResponse.ok && !upstreamResponse.ok) {
      notes.push(
        "Next.js route succeeded while upstream failed; proxy layer may be configured but backend rejected the request.",
      );
    } else if (!nextResponse.ok && upstreamResponse.ok) {
      notes.push(
        "Upstream succeeded while Next.js route failed; inspect the Next.js API handler or environment variables.",
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

  if (!threadId) {
    logger.error(
      "Usage: yarn tsx scripts/test-feature-graph-develop.ts --thread-id <id> [--feature-id <id>] [--repo <path>] [--web-url <url>] [--langgraph-api <url>] [--skip-upstream]",
    );
    process.exit(1);
  }

  const repoPath = resolveRepoPath(args.repoPath);
  const graph = await loadGraph(repoPath);
  const featureSelection = selectFeature(
    graph,
    args.featureId ?? process.env.FEATURE_GRAPH_FEATURE_ID,
  );
  const featureId = featureSelection.featureId;

  const webUrl = resolveWebApiUrl(args.webUrl);
  const langgraphApiUrl = resolveLangGraphApiUrl(args.langgraphApiUrl);
  const localMode = process.env.OPEN_SWE_LOCAL_MODE === "true";

  logger.info("Starting feature graph development check", {
    threadId,
    featureId,
    webUrl,
    langgraphApiUrl,
    repoPath,
    localMode,
  });

  const managerState = await fetchManagerState(
    new Client({
      apiUrl: langgraphApiUrl,
      defaultHeaders: localMode ? { [LOCAL_MODE_HEADER]: "true" } : undefined,
    }),
    threadId,
  );
  const configurableFields =
    getCustomConfigurableFields({
      configurable: managerState?.metadata?.configurable as GraphConfig["configurable"],
    } as GraphConfig) ?? {};

  if (managerState && Object.keys(configurableFields).length === 0) {
    logger.info("No configurable fields detected in manager state metadata.");
  }

  if (!featureId) {
    logger.error("No feature_id available for request payload");
  }

  const nextResponse = featureId
    ? await requestNextApi({ webUrl, threadId, featureId })
    : { ok: false, status: 0, payload: null, rawBody: "", reason: "Missing feature_id" };

  const nextValidation = validateResponseShape(nextResponse);

  let upstreamResponse: ParsedResponse | undefined;
  let upstreamValidation: ResponseValidation | undefined;

  if (!args.skipUpstream && featureId) {
    upstreamResponse = await requestUpstream({
      langgraphApiUrl,
      featureId,
      threadId,
      localMode,
    });
    upstreamValidation = validateResponseShape(upstreamResponse);
  } else if (args.skipUpstream) {
    logger.info("Skipping upstream /feature-graph/develop call");
  }

  const diagnosticNotes = buildDiagnosticNotes({
    graph,
    featureSelectionNotes: featureSelection.notes,
    targetFeatureId: featureId,
    managerState,
    nextResponse,
    upstreamResponse,
    skippedUpstream: Boolean(args.skipUpstream),
    nextValidation,
    upstreamValidation,
  });

  if (diagnosticNotes.length > 0) {
    logger.info("Failure reasoning", { notes: diagnosticNotes });
  } else {
    logger.info("No failure indicators detected.");
  }
}

main().catch((error) => {
  logger.error("Feature graph develop test script failed", { error });
  process.exit(1);
});
