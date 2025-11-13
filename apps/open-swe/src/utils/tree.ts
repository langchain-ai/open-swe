import { getCurrentTaskInput } from "@langchain/langgraph";
import {
  GraphState,
  TargetRepository,
  GraphConfig,
} from "@openswe/shared/open-swe/types";
import { createLogger, LogLevel } from "./logger.js";
import path from "node:path";
import { SANDBOX_ROOT_DIR, TIMEOUT_SEC } from "@openswe/shared/constants";
import { getSandboxErrorFields } from "./sandbox-error-fields.js";
import { isLocalMode } from "@openswe/shared/open-swe/local-mode";
import { createShellExecutor } from "./shell-executor/index.js";
import type {
  ArtifactCollection,
  ArtifactRef,
  FeatureNode,
} from "@openswe/shared/feature-graph/types";

const logger = createLogger(LogLevel.INFO, "Tree");

export const FAILED_TO_GENERATE_TREE_MESSAGE =
  "Failed to generate tree. Please try again.";

export type FeatureTreeOptions = {
  mode?: "annotate" | "filter" | "annotate-and-filter";
  features?: FeatureNode[];
  featureIds?: string[];
};

const normalizePathCandidate = (value: string): string | undefined => {
  if (!value) {
    return undefined;
  }

  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }

  if (/^[a-z]+:\/\//i.test(trimmed)) {
    return undefined;
  }

  const replaced = trimmed.replace(/\\/g, "/");
  if (replaced.startsWith("../")) {
    return undefined;
  }

  const normalized = path.posix
    .normalize(replaced)
    .replace(/^\.\/?/, "")
    .replace(/\/$/, "");

  if (!normalized || normalized === ".") {
    return undefined;
  }

  if (
    !normalized.includes("/") &&
    !/\.[a-z0-9]+$/i.test(normalized) &&
    !trimmed.endsWith("/")
  ) {
    return undefined;
  }

  return normalized;
};

type FlattenedArtifact = { key?: string; ref: ArtifactRef };

const flattenArtifactCollection = (
  collection: ArtifactCollection | undefined,
): FlattenedArtifact[] => {
  if (!collection) {
    return [];
  }

  if (Array.isArray(collection)) {
    return collection.map((ref) => ({ ref }));
  }

  return Object.entries(collection).flatMap(([key, ref]) => {
    if (Array.isArray(ref)) {
      return ref.map((value) => ({ key, ref: value }));
    }
    return [{ key, ref }];
  });
};

const artifactRefToPaths = (ref: ArtifactRef, key?: string): string[] => {
  const candidates = new Set<string>();
  const push = (value?: string) => {
    const normalized = value ? normalizePathCandidate(value) : undefined;
    if (normalized) {
      candidates.add(normalized);
    }
  };

  if (typeof ref === "string") {
    push(ref);
  } else {
    push(ref.path);
    if (!ref.path) {
      push(ref.name);
      push(ref.description);
      push(key);
    }
    if (!ref.path && ref.metadata) {
      for (const nested of collectMetadataPaths(ref.metadata)) {
        push(nested);
      }
    }
  }

  return Array.from(candidates);
};

const collectMetadataPaths = (value: unknown): string[] => {
  if (typeof value === "string") {
    const normalized = normalizePathCandidate(value);
    return normalized ? [normalized] : [];
  }

  if (Array.isArray(value)) {
    return value.flatMap((entry) => collectMetadataPaths(entry));
  }

  if (typeof value === "object" && value !== null) {
    return Object.values(value).flatMap((entry) => collectMetadataPaths(entry));
  }

  return [];
};

const collectFeaturePathMap = (
  options?: FeatureTreeOptions,
): Map<string, Set<string>> => {
  const map = new Map<string, Set<string>>();
  if (!options) {
    return map;
  }

  const allowedFeatureIds = options.featureIds?.length
    ? new Set(options.featureIds.map((id) => id.trim().toLowerCase()).filter(Boolean))
    : undefined;

  for (const feature of options.features ?? []) {
    if (!feature) continue;
    const normalizedFeatureId = feature.id.trim();
    if (!normalizedFeatureId) continue;

    if (
      allowedFeatureIds &&
      !allowedFeatureIds.has(normalizedFeatureId.toLowerCase())
    ) {
      continue;
    }

    const candidatePaths = new Set<string>();
    for (const entry of flattenArtifactCollection(feature.artifacts)) {
      for (const pathCandidate of artifactRefToPaths(entry.ref, entry.key)) {
        candidatePaths.add(pathCandidate);
      }
    }

    if (feature.metadata) {
      for (const metadataPath of collectMetadataPaths(feature.metadata)) {
        candidatePaths.add(metadataPath);
      }
    }

    for (const pathCandidate of candidatePaths) {
      if (!map.has(pathCandidate)) {
        map.set(pathCandidate, new Set());
      }
      map.get(pathCandidate)!.add(normalizedFeatureId);
    }
  }

  return map;
};

const applyFeatureTreeOptions = (
  tree: string,
  options?: FeatureTreeOptions,
): string => {
  const pathMap = collectFeaturePathMap(options);
  if (pathMap.size === 0) {
    return tree;
  }

  const mode = options?.mode ?? "annotate";
  const shouldAnnotate = mode === "annotate" || mode === "annotate-and-filter";
  const shouldFilter = mode === "filter" || mode === "annotate-and-filter";

  const pathEntries = Array.from(pathMap.entries());
  const lines = tree.split(/\r?\n/);
  const pathStack: string[] = [];
  const pathToLine = new Map<string, number>();
  const keepLines = new Set<number>();
  const annotatedLines: { line: string; matches: Set<string>; index: number }[] = [];
  let rootIndex: number | undefined;

  for (let index = 0; index < lines.length; index += 1) {
    let line = lines[index];
    const trimmed = line.trim();

    if (trimmed === "." || trimmed === "./") {
      rootIndex = index;
      keepLines.add(index);
      pathStack.length = 0;
      annotatedLines.push({ line, matches: new Set(), index });
      continue;
    }

    const match = line.match(/^([│\s]*)(├── |└── )(.*)$/);
    if (!match) {
      annotatedLines.push({ line, matches: new Set(), index });
      continue;
    }

    const [, prefix, _connector, namePart] = match;
    const depth = Math.floor(prefix.length / 4) + 1;
    const name = namePart.replace(/\s+\[.*$/, "").trim();
    const parentPath = depth > 1 ? pathStack[depth - 2] : "";
    const currentPath = parentPath ? `${parentPath}/${name}` : name;

    pathStack.length = depth;
    pathStack[depth - 1] = currentPath;
    pathToLine.set(currentPath, index);

    const matches = new Set<string>();
    for (const [featurePath, featureIds] of pathEntries) {
      if (
        featurePath === currentPath ||
        featurePath.startsWith(`${currentPath}/`) ||
        currentPath.startsWith(`${featurePath}/`)
      ) {
        for (const id of featureIds) {
          matches.add(id);
        }
      }
    }

    if (matches.size > 0) {
      keepLines.add(index);
      let ancestorDepth = depth - 2;
      while (ancestorDepth >= 0) {
        const ancestorPath = pathStack[ancestorDepth];
        if (!ancestorPath) {
          ancestorDepth -= 1;
          continue;
        }
        const ancestorIndex = pathToLine.get(ancestorPath);
        if (ancestorIndex !== undefined) {
          keepLines.add(ancestorIndex);
        }
        ancestorDepth -= 1;
      }
    }

    if (matches.size > 0 && shouldAnnotate) {
      const label = `feature${matches.size > 1 ? "s" : ""}: ${Array.from(matches).join(", ")}`;
      line = `${line}  ← ${label}`;
    }

    annotatedLines.push({ line, matches, index });
  }

  if (rootIndex !== undefined) {
    keepLines.add(rootIndex);
  }

  if (!shouldFilter) {
    return annotatedLines.map((entry) => entry.line).join("\n");
  }

  return annotatedLines
    .filter((entry) => keepLines.has(entry.index) || entry.line.trim() === "")
    .map((entry) => entry.line)
    .join("\n");
};

export async function getCodebaseTree(
  config: GraphConfig,
  sandboxSessionId_?: string,
  targetRepository_?: TargetRepository,
  options?: FeatureTreeOptions,
): Promise<string> {
  try {
    const command = `git ls-files | tree --fromfile -L 5`;
    let sandboxSessionId = sandboxSessionId_;
    let targetRepository = targetRepository_;

    // Check if we're in local mode
    if (isLocalMode(config)) {
      return getCodebaseTreeLocal(config, options);
    }

    // If sandbox session ID is not provided, try to get it from the current state.
    if (!sandboxSessionId || !targetRepository) {
      try {
        const state = getCurrentTaskInput<GraphState>();
        // Prefer the provided sandbox session ID and target repository. Fallback to state if defined.
        sandboxSessionId = sandboxSessionId ?? state.sandboxSessionId;
        targetRepository = targetRepository ?? state.targetRepository;
      } catch {
        // not executed in a LangGraph instance. continue.
      }
    }

    if (!sandboxSessionId) {
      logger.error("Failed to generate tree: No sandbox session ID provided");
      throw new Error("Failed generate tree: No sandbox session ID provided");
    }
    if (!targetRepository) {
      logger.error("Failed to generate tree: No target repository provided");
      throw new Error("Failed generate tree: No target repository provided");
    }

    const executor = createShellExecutor(config);
    const repoDir = path.join(SANDBOX_ROOT_DIR, targetRepository.repo);
    const response = await executor.executeCommand({
      command,
      workdir: repoDir,
      timeout: TIMEOUT_SEC,
      sandboxSessionId,
    });

    if (response.exitCode !== 0) {
      logger.error("Failed to generate tree", {
        exitCode: response.exitCode,
        result: response.result ?? response.artifacts?.stdout,
      });
      throw new Error(
        `Failed to generate tree: ${response.result ?? response.artifacts?.stdout}`,
      );
    }

    return applyFeatureTreeOptions(response.result, options);
  } catch (e) {
    const errorFields = getSandboxErrorFields(e);
    logger.error("Failed to generate tree", {
      ...(errorFields ? { errorFields } : {}),
      ...(e instanceof Error
        ? {
            name: e.name,
            message: e.message,
            stack: e.stack,
          }
        : {}),
    });
    return FAILED_TO_GENERATE_TREE_MESSAGE;
  }
}

/**
 * Local version of getCodebaseTree using ShellExecutor
 */
async function getCodebaseTreeLocal(
  config: GraphConfig,
  options?: FeatureTreeOptions,
): Promise<string> {
  try {
    const executor = createShellExecutor(config);
    const command = `git ls-files | tree --fromfile -L 5`;

    const response = await executor.executeCommand({
      command,
      timeout: TIMEOUT_SEC,
    });

    if (response.exitCode !== 0) {
      logger.error("Failed to generate tree in local mode", {
        exitCode: response.exitCode,
        result: response.result,
      });
      throw new Error(
        `Failed to generate tree in local mode: ${response.result}`,
      );
    }

    return applyFeatureTreeOptions(response.result, options);
  } catch (e) {
    logger.error("Failed to generate tree in local mode", {
      ...(e instanceof Error
        ? {
            name: e.name,
            message: e.message,
            stack: e.stack,
          }
        : { error: e }),
    });
    return FAILED_TO_GENERATE_TREE_MESSAGE;
  }
}
