import { NextRequest, NextResponse } from "next/server";

import { LOCAL_MODE_HEADER } from "@openswe/shared/constants";

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

function resolveFeatureId(body: Record<string, unknown>): string | null {
  const candidate = body.feature_id ?? body.featureId;
  if (typeof candidate === "string" && candidate.trim()) {
    return candidate.trim();
  }
  return null;
}

function resolveProposalId(body: Record<string, unknown>): string | null {
  const candidate = body.proposal_id ?? body.proposalId;
  if (typeof candidate === "string" && candidate.trim()) {
    return candidate.trim();
  }
  return null;
}

function resolveAction(body: Record<string, unknown>): string | null {
  const candidate = body.action;
  if (
    candidate === "approve" ||
    candidate === "reject" ||
    candidate === "info"
  ) {
    return candidate;
  }
  return null;
}

function resolveRationale(body: Record<string, unknown>): string | undefined {
  const candidate = body.rationale;
  if (typeof candidate === "string" && candidate.trim()) {
    return candidate.trim();
  }
  return undefined;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body = (await request.json()) as Record<string, unknown>;
    const threadId =
      resolveThreadId(body?.thread_id) ?? resolveThreadId(body?.threadId);
    const featureId = resolveFeatureId(body);
    const proposalId = resolveProposalId(body);
    const action = resolveAction(body);
    const rationale = resolveRationale(body);

    if (!threadId) {
      return NextResponse.json(
        { error: "thread_id is required" },
        { status: 400 },
      );
    }

    if (!featureId && !proposalId) {
      return NextResponse.json(
        { error: "feature_id or proposal_id is required" },
        { status: 400 },
      );
    }

    if (!action) {
      return NextResponse.json(
        { error: "action must be approve, reject, or info" },
        { status: 400 },
      );
    }

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };

    if (process.env.OPEN_SWE_LOCAL_MODE === "true") {
      headers[LOCAL_MODE_HEADER] = "true";
    }

    const upstream = await fetch(`${resolveApiUrl()}/feature-graph/proposal`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        thread_id: threadId,
        feature_id: featureId,
        proposal_id: proposalId,
        action,
        rationale,
      }),
    });

    const rawBody = await upstream.text();
    let payload: unknown = null;

    try {
      payload = rawBody ? JSON.parse(rawBody) : null;
    } catch {
      payload = null;
    }

      if (!upstream.ok) {
        const errorPayload = payload as { error?: unknown } | null;
        const message =
          (errorPayload && typeof errorPayload.error === "string"
            ? errorPayload.error
            : rawBody || upstream.statusText || "Failed to process proposal") ??
          "Failed to process proposal";

      return NextResponse.json(
        {
          error: message,
          upstream: {
            status: upstream.status,
            message: rawBody,
          },
        },
        { status: upstream.status },
      );
    }

    return NextResponse.json(payload ?? {});
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to process proposal";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
