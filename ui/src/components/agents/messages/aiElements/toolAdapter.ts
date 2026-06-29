import type { AcpToolKind, AcpToolStatus } from "@/lib/agents/types";

/**
 * AI Elements' Tool/ToolHeader is typed around the Vercel AI SDK's
 * `ToolUIPart["state"]`. We mirror that union locally (the installed
 * `tool.tsx` does the same, so it needs no `ai` dependency) and map our
 * LangGraph tool-call status onto it. `pending` is our approval gate, which
 * lines up with the SDK's `approval-requested`.
 */
export type ToolHeaderState =
  | "input-streaming"
  | "input-available"
  | "approval-requested"
  | "approval-responded"
  | "output-available"
  | "output-denied"
  | "output-error";

export function toolStatusToHeaderState(status: AcpToolStatus): ToolHeaderState {
  switch (status) {
    case "pending":
      return "approval-requested";
    case "in_progress":
      return "input-available";
    case "completed":
      return "output-available";
    case "error":
      return "output-error";
    default: {
      const exhaustive: never = status;
      return exhaustive;
    }
  }
}

/** The `tool-${string}` discriminator AI Elements' ToolHeader expects as `type`. */
export function toolHeaderType(kind: AcpToolKind): `tool-${AcpToolKind}` {
  return `tool-${kind}`;
}
