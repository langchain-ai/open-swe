import { NextRequest, NextResponse } from "next/server";
import { Client } from "@langchain/langgraph-sdk";
import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";
import type { ManagerGraphState } from "@openswe/shared/open-swe/manager/types";

import { mapFeatureGraphPayload } from "@/lib/feature-graph-payload";

function resolveApiUrl(): string {
  return (
    process.env.LANGGRAPH_API_URL ??
    process.env.NEXT_PUBLIC_API_URL ??
    "http://localhost:2024"
  );
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

function resolveConfigurable(
  value: unknown,
): Record<string, unknown> | undefined {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return undefined;
}

async function requestGraphGeneration({
  workspaceAbsPath,
  prompt,
  configurable,
}: {
  workspaceAbsPath: string;
  prompt: string;
  configurable?: Record<string, unknown>;
}): Promise<{
  ok: boolean;
  status: number;
  payload: unknown;
  message: string;
}> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };

  if (process.env.OPEN_SWE_LOCAL_MODE === "true") {
    headers[LOCAL_MODE_HEADER] = "true";
  }

  const response = await fetch(`${resolveApiUrl()}/feature-graph/generate`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      workspaceAbsPath,
      prompt,
      configurable,
    }),
  });

  const payload = await response.json().catch(() => null);
  const message =
    (payload && typeof (payload as { error?: unknown })?.error === "string"
      ? (payload as { error: string }).error
      : response.statusText || "Failed to generate feature graph") ??
    "Failed to generate feature graph";

  return { ok: response.ok, status: response.status, payload, message };
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

    const managerState = await client.threads.getState<ManagerGraphState>(
      threadId,
    );

    if (!managerState?.values) {
      return NextResponse.json(
        { error: "Manager state not found for thread" },
        { status: 404 },
      );
    }

    const workspaceAbsPath =
      managerState.values.workspaceAbsPath ?? managerState.values.workspacePath;
    if (!workspaceAbsPath) {
      return NextResponse.json(
        { error: "Workspace path unavailable for this thread" },
        { status: 400 },
      );
    }

    const generation = await requestGraphGeneration({
      workspaceAbsPath,
      prompt,
      configurable: resolveConfigurable(managerState.metadata?.configurable),
    });

    if (!generation.ok) {
      return NextResponse.json(
        { error: generation.message },
        { status: generation.status },
      );
    }

    const payload = generation.payload;

    const { graph, activeFeatureIds } = mapFeatureGraphPayload(payload);

    if (!graph) {
      return NextResponse.json(
        { error: "Generated feature graph payload was invalid" },
        { status: 500 },
      );
    }

    await client.threads.updateState<ManagerGraphState>(threadId, {
      values: {
        ...managerState.values,
        featureGraph: graph,
        activeFeatureIds,
      },
    });

    return NextResponse.json({
      featureGraph:
        payload?.featureGraph ?? payload?.feature_graph ?? payload?.graph,
      activeFeatureIds,
    });
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Failed to generate feature graph";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
