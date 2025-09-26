import type { Hono } from "hono";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import { resolveWorkspacePath } from "../../utils/workspace.js";
import { createLogger, LogLevel } from "../../utils/logger.js";

const logger = createLogger(LogLevel.INFO, "RunRoute");

class RunConfigurationError extends Error {
  public readonly status: ContentfulStatusCode;

  constructor(message: string, status: ContentfulStatusCode) {
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
    const requestStartedAt = Date.now();
    logger.info("Received run request", {
      pathProvided: ctx.req.header("content-type")?.includes("json"),
    });
    let body: Record<string, unknown>;

    try {
      body = await ctx.req.json<Record<string, unknown>>();
      logger.info("Parsed run request payload", {
        workspaceAbsPath: body.workspaceAbsPath,
      });
    } catch (error) {
      logger.error("Invalid JSON payload", {
        error: error instanceof Error ? error.message : String(error),
      });
      return ctx.json(
        {
          error: `Invalid JSON payload: ${String(
            error instanceof Error ? error.message : error,
          )}.`,
        },
        400 as ContentfulStatusCode,
      );
    }

    try {
      const resolvedWorkspaceAbsPath = resolveInsideRoot(
        typeof body.workspaceAbsPath === "string"
          ? body.workspaceAbsPath
          : undefined,
      );

      logger.info("Workspace path resolved", {
        resolvedWorkspaceAbsPath,
        durationMs: Date.now() - requestStartedAt,
      });

      return ctx.json({ resolvedWorkspaceAbsPath });
    } catch (error) {
      if (error instanceof RunConfigurationError) {
        logger.error("Workspace path validation failed", {
          status: error.status,
          message: error.message,
          durationMs: Date.now() - requestStartedAt,
        });
        return ctx.json({ error: error.message }, error.status);
      }

      logger.error("Unexpected error handling run request", {
        error: error instanceof Error ? error.message : String(error),
        durationMs: Date.now() - requestStartedAt,
      });
      throw error;
    }
  });
}
