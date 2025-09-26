import type { Hono } from "hono";
import { resolveWorkspacePath } from "../../utils/workspace.js";

class RunConfigurationError extends Error {
  public readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function resolveInsideRoot(
  workspaceAbsPath: string | undefined,
): string {
  try {
    return resolveWorkspacePath(workspaceAbsPath);
  } catch (error) {
    if (error instanceof Error) {
      const message = error.message;
      if (message.includes("WORKSPACES_ROOT")) {
        throw new RunConfigurationError(message, 500);
      }
      throw new RunConfigurationError(message, 400);
    }
    throw error;
  }
}

export function registerRunRoute(app: Hono) {
  app.post("/run", async (ctx) => {
    let body: Record<string, unknown>;

    try {
      body = await ctx.req.json<Record<string, unknown>>();
    } catch (error) {
      return ctx.json(
        {
          error: `Invalid JSON payload: ${String(
            error instanceof Error ? error.message : error,
          )}.`,
        },
        400,
      );
    }

    try {
      const resolvedWorkspaceAbsPath = resolveInsideRoot(
        typeof body.workspaceAbsPath === "string"
          ? body.workspaceAbsPath
          : undefined,
      );

      return ctx.json({ resolvedWorkspaceAbsPath });
    } catch (error) {
      if (error instanceof RunConfigurationError) {
        return ctx.json({ error: error.message }, error.status);
      }

      throw error;
    }
  });
}
