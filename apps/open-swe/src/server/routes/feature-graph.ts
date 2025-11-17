import type { Hono } from "hono";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import { GraphConfig } from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "../../utils/logger.js";
import { resolveInsideRoot } from "./run.js";
import { generateFeatureGraphForWorkspace } from "../../graphs/manager/utils/generate-feature-graph.js";

const logger = createLogger(LogLevel.INFO, "FeatureGraphRoute");

type GenerateRequestBody = {
  workspaceAbsPath?: unknown;
  configurable?: Record<string, unknown>;
};

export function registerFeatureGraphRoute(app: Hono) {
  app.post("/feature-graph/generate", async (ctx) => {
    let body: GenerateRequestBody;

    try {
      body = await ctx.req.json<GenerateRequestBody>();
    } catch (error) {
      logger.error("Invalid JSON payload for feature graph generation", {
        error: error instanceof Error ? error.message : String(error),
      });
      return ctx.json(
        { error: "Invalid JSON payload." },
        400 as ContentfulStatusCode,
      );
    }

    const workspaceAbsPath =
      typeof body.workspaceAbsPath === "string" ? body.workspaceAbsPath : undefined;

    if (!workspaceAbsPath) {
      return ctx.json(
        { error: "workspaceAbsPath is required" },
        400 as ContentfulStatusCode,
      );
    }

    try {
      const resolvedWorkspaceAbsPath = resolveInsideRoot(workspaceAbsPath);
      const config: GraphConfig = {
        configurable: {
          workspacePath: resolvedWorkspaceAbsPath,
          ...(body.configurable ?? {}),
        },
      } as GraphConfig;

      const graphPath = `${resolvedWorkspaceAbsPath}/features/graph/graph.yaml`;
      const generated = await generateFeatureGraphForWorkspace({
        workspacePath: resolvedWorkspaceAbsPath,
        graphPath,
        config,
      });

      return ctx.json({
        featureGraph: generated.graphFile,
        activeFeatureIds: generated.activeFeatureIds,
      });
    } catch (error) {
      logger.error("Failed to generate feature graph", {
        error: error instanceof Error ? error.message : String(error),
      });
      return ctx.json(
        { error: "Failed to generate feature graph." },
        500 as ContentfulStatusCode,
      );
    }
  });
}
