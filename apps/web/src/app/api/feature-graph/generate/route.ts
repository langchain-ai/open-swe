import { NextRequest, NextResponse } from "next/server";
import { Client } from "@langchain/langgraph-sdk";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";
import type { GraphConfig } from "@openswe/shared/open-swe/types";
import { getCustomConfigurableFields } from "@openswe/shared/open-swe/utils/config";
import { createLogger, LogLevel } from "@openswe/shared/logger";

import { mapFeatureGraphPayload } from "@/lib/feature-graph-payload";

const logger = createLogger(LogLevel.INFO, "FeatureGraphGenerateRoute");

class ApiConfigError extends Error {}
class ServiceUnavailableError extends Error {}

function resolveApiUrl(): string {
  const apiUrl =
    process.env.LANGGRAPH_API_URL?.trim() ??
    process.env.NEXT_PUBLIC_API_URL?.trim();

  if (!apiUrl) {
    throw new ApiConfigError(
      "LangGraph API URL not configured. Set LANGGRAPH_API_URL or NEXT_PUBLIC_API_URL.",
    );
  }

  try {
    new URL(apiUrl);
  } catch {
    throw new ApiConfigError(`Invalid LangGraph API URL: ${apiUrl}`);
  }

  return apiUrl;
}

function resolveThreadId(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) {
    return value.trim();
  }
  return null;
}

function resolvePrompt(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  return "";
}

async function requestGraphGeneration({
  threadId,
  workspaceAbsPath,
  prompt,
  configurable,
}: {
  threadId: string;
  workspaceAbsPath: string;
  prompt: string;
  configurable?: Record<string, unknown>;
}): Promise<{
  ok: boolean;
  status: number;
  payload: unknown;
  message: string;
  rawBody?: string;
}> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (process.env.OPEN_SWE_LOCAL_MODE === "true") {
    headers[LOCAL_MODE_HEADER] = "true";
  }

  logger.info("Requesting feature graph generation", {
    threadId,
    workspaceAbsPath,
    configurablePresent: Boolean(configurable),
  });

  const response = await fetch(`${resolveApiUrl()}/feature-graph/generate`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      workspaceAbsPath,
      prompt,
      configurable,
    }),
  }).catch((error) => {
    const message =
      error instanceof Error && error.message
        ? error.message
        : "Failed to reach LangGraph API";
    throw new ServiceUnavailableError(message);
  });

  const rawBody = await response.text();
  let payload: unknown = null;

  try {
    payload = rawBody ? JSON.parse(rawBody) : null;
  } catch (error) {
    logger.warn("Failed to parse feature graph generation response", {
      threadId,
      workspaceAbsPath,
      error,
    });
  }

  const message =
    (payload && typeof (payload as { error?: unknown })?.error === "string"
      ? (payload as { error: string }).error
      : rawBody || response.statusText || "Failed to generate feature graph") ??
    "Failed to generate feature graph";

  return {
    ok: response.ok,
    status: response.status,
    payload,
    message,
    rawBody,
  };
}

function redactMessage(message: string, workspaceAbsPath?: string): string {
  if (!workspaceAbsPath) {
    return message;
  }

  return message.replaceAll(workspaceAbsPath, "[redacted]");
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body = await request.json();
    const threadId =
      resolveThreadId(body?.thread_id) ?? resolveThreadId(body?.threadId);
    const prompt = resolvePrompt(body?.prompt);

    if (!threadId) {
      return NextResponse.json(
        { error: "thread_id is required" },
        { status: 400 },
      );
    }

    if (!prompt) {
      return NextResponse.json(
        { error: "prompt is required" },
        { status: 400 },
      );
    }

    const client = new Client({
      apiUrl: resolveApiUrl(),
      defaultHeaders:
        process.env.OPEN_SWE_LOCAL_MODE === "true"
          ? { [LOCAL_MODE_HEADER]: "true" }
          : undefined,
    });

    let managerThreadState: Awaited<ReturnType<typeof client.threads.getState<ManagerGraphState>>> | null =
      null;
    try {
      managerThreadState = await client.threads.getState<ManagerGraphState>(threadId);
    } catch (error) {
      const status = (error as { status?: number })?.status;
      const message =
        status === 404
          ? "Manager state not found for thread"
          : `LangGraph backend unreachable: ${
              (error as { message?: string })?.message ?? "Unknown error"
            }`;

      if (status === 404) {
        return NextResponse.json({ error: message }, { status: 404 });
      }

      throw new ServiceUnavailableError(message);
    }

    if (!managerThreadState?.values) {
      return NextResponse.json(
        { error: "Manager state not found for thread" },
        { status: 404 },
      );
    }

    const workspaceAbsPath =
      managerThreadState.values.workspaceAbsPath ??
      managerThreadState.values.workspacePath;
    if (!workspaceAbsPath) {
      return NextResponse.json(
        { error: "Workspace path unavailable for this thread" },
        { status: 400 },
      );
    }

    const configurableFields =
      getCustomConfigurableFields({
        configurable: managerThreadState.metadata
          ?.configurable as GraphConfig["configurable"],
      } as GraphConfig) ?? {};

    const generation = await requestGraphGeneration({
      threadId,
      workspaceAbsPath,
      prompt,
      configurable:
        Object.keys(configurableFields).length > 0
          ? configurableFields
          : undefined,
    });

    if (!generation.ok) {
      const redactedMessage = redactMessage(generation.message, workspaceAbsPath);
      const redactedRawBody = redactMessage(generation.rawBody ?? "", workspaceAbsPath);

      return NextResponse.json(
        {
          error: redactedMessage,
          upstream: {
            status: generation.status,
            message: redactedRawBody || undefined,
          },
        },
        { status: generation.status },
      );
    }

    const payload = generation.payload;

    const { graph, activeFeatureIds } = mapFeatureGraphPayload(payload);

    if (!graph) {
      logger.error("Feature graph generation payload was invalid", {
        threadId,
        workspaceAbsPath,
        payload,
      });

      return NextResponse.json(
        { error: "Generated feature graph payload was invalid" },
        { status: 500 },
      );
    }

    await client.threads.updateState<ManagerGraphState>(threadId, {
      values: {
        ...managerThreadState.values,
        featureGraph: graph,
        activeFeatureIds,
      },
      asNode: "feature-graph-orchestrator",
    });

    return NextResponse.json({
      featureGraph: graph,
      activeFeatureIds,
    });
  } catch (error) {
    if (error instanceof ApiConfigError) {
      logger.error("Feature graph generation configuration error", { error });
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    if (error instanceof ServiceUnavailableError) {
      logger.error("LangGraph backend unavailable for feature graph", { error });
      return NextResponse.json({ error: error.message }, { status: 503 });
    }

    const message =
      error instanceof Error
        ? error.message
        : "Failed to generate feature graph";

    logger.error("Failed to handle feature graph generation request", {
      error,
    });
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
