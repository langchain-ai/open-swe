import fs from "node:fs";
import path from "node:path";
import type { Hono } from "hono";

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
  const workspacesRoot = process.env.WORKSPACES_ROOT;
  if (!workspacesRoot) {
    throw new RunConfigurationError(
      "WORKSPACES_ROOT environment variable is not set.",
      500,
    );
  }

  if (!workspaceAbsPath || workspaceAbsPath.trim().length === 0) {
    throw new RunConfigurationError(
      "workspaceAbsPath is required.",
      400,
    );
  }

  let resolvedRoot: string;
  let resolvedPath: string;
  try {
    resolvedRoot = fs.realpathSync(workspacesRoot);
  } catch (error) {
    throw new RunConfigurationError(
      `Unable to resolve workspaces root: ${String(
        error instanceof Error ? error.message : error,
      )}.`,
      500,
    );
  }

  try {
    resolvedPath = fs.realpathSync(workspaceAbsPath);
  } catch (error) {
    throw new RunConfigurationError(
      `Unable to resolve workspace path: ${String(
        error instanceof Error ? error.message : error,
      )}.`,
      400,
    );
  }

  const normalizedRoot = resolvedRoot.endsWith(path.sep)
    ? resolvedRoot
    : `${resolvedRoot}${path.sep}`;

  if (
    resolvedPath !== resolvedRoot &&
    !resolvedPath.startsWith(normalizedRoot)
  ) {
    throw new RunConfigurationError(
      `Resolved workspace path "${resolvedPath}" is outside of the configured root "${resolvedRoot}".`,
      400,
    );
  }

  return resolvedPath;
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
