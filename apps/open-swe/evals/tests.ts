// TODO: Add ruff promise and the mypy promise to the tests.
import path from "node:path";
import type { Sandbox } from "../src/utils/sandbox.js";
import { createLogger, LogLevel } from "../src/utils/logger.js";
import {
  ExecOptions,
  FeatureScopeOptions,
  RuffResult,
  RuffIssue,
  MyPyResult,
} from "./open-swe-types.js";
import {
  FeatureGraph,
  FeatureNode,
  loadFeatureGraph,
  impactedFeaturesByCodeChange,
} from "@openswe/shared/feature-graph";
import { execInSandbox, type ExecResult } from "@openswe/sandbox-core";

const logger = createLogger(LogLevel.DEBUG, " Evaluation Tests");

type FeatureScopeResolution = {
  features: FeatureNode[];
  changedPaths: string[];
  artifactPaths: string[];
  pathFilters: string[];
  env: Record<string, string>;
};

const FEATURE_SCOPE_ENV_FLAG = "OPEN_SWE_FEATURE_SCOPED_EVAL";
const FEATURE_SCOPE_REQUIRE_MATCH_FLAG = "OPEN_SWE_FEATURE_SCOPE_REQUIRE_MATCH";
const FEATURE_SCOPE_DEBUG_FLAG = "OPEN_SWE_FEATURE_SCOPE_DEBUG";
const DEFAULT_GRAPH_RELATIVE_PATH = path.join(
  "features",
  "graph",
  "graph.yaml",
);
const MAX_PATH_FILTERS = 200;

const featureScopeCache = new Map<string, Promise<FeatureScopeResolution | null>>();

const parseBooleanFlag = (value: string | undefined, fallback: boolean): boolean => {
  if (value === undefined) return fallback;
  const normalized = value.trim().toLowerCase();
  if (["1", "true", "yes", "y", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "n", "off"].includes(normalized)) return false;
  return fallback;
};

const isFeatureScopeEnabled = (options?: FeatureScopeOptions): boolean => {
  const base = options?.enabled ?? false;
  return parseBooleanFlag(process.env[FEATURE_SCOPE_ENV_FLAG], base);
};

const shouldRequireScopeMatch = (): boolean =>
  parseBooleanFlag(process.env[FEATURE_SCOPE_REQUIRE_MATCH_FLAG], false);

const isFeatureScopeDebug = (): boolean =>
  parseBooleanFlag(process.env[FEATURE_SCOPE_DEBUG_FLAG], false);

const mergeEnvironment = (
  base: Record<string, string> | undefined,
  additions: Record<string, string> | undefined,
): Record<string, string> | undefined => {
  if (!base && !additions) return base;
  if (!base) return additions ? { ...additions } : undefined;
  if (!additions) return base;
  return { ...base, ...additions };
};

const normalizeRelativePath = (candidate: string): string => {
  const trimmed = candidate.trim();
  if (!trimmed) return "";
  return trimmed.replace(/\\/g, "/").replace(/^\.\//, "");
};

const sanitizePath = (candidate: string): string | null => {
  const normalized = normalizeRelativePath(candidate);
  if (!normalized) return null;
  if (/^[a-z]+:\/\//i.test(normalized)) return null;
  if (normalized.startsWith("../")) return null;
  if (normalized.startsWith("#")) return null;
  return normalized;
};

const dedupe = <T>(values: Iterable<T>): T[] => {
  const seen = new Set<T>();
  const result: T[] = [];
  for (const value of values) {
    if (seen.has(value)) continue;
    seen.add(value);
    result.push(value);
  }
  return result;
};

const isPythonPath = (candidate: string): boolean =>
  /\.py[i]?$/u.test(candidate);

const isLikelyPythonTarget = (candidate: string): boolean => {
  if (isPythonPath(candidate)) return true;
  return /(^|\/)(tests?|test_utils|integration)(\/|$)/u.test(candidate);
};

const SAFE_PATH_PATTERN = /^[-\w@/.]+$/u;
const SHELL_ESCAPE_PATTERN = /(["\\$`])/g;

const quotePath = (candidate: string): string => {
  if (SAFE_PATH_PATTERN.test(candidate)) return candidate;
  return `"${candidate.replace(SHELL_ESCAPE_PATTERN, "\\$1")}"`;
};

const injectPathFiltersIntoCommand = (
  command: string,
  paths: string[],
): string => {
  if (paths.length === 0) return command;
  const serialized = paths.map(quotePath).join(" ");
  const replacements: [RegExp, string][] = [
    [/\bcheck\s+\.(?=\s|$)/u, `check ${serialized}`],
    [/\bmypy\s+\.(?=\s|$)/u, `mypy ${serialized}`],
  ];

  let next = command;
  for (const [pattern, replacement] of replacements) {
    if (pattern.test(next)) {
      next = next.replace(pattern, replacement);
    }
  }

  if (next === command) {
    next = `${command} ${serialized}`;
  }

  return next;
};

const collectArtifactPaths = (feature: FeatureNode): string[] => {
  if (!feature.artifacts) return [];

  const pushPaths = (value: unknown, bucket: string[]) => {
    if (typeof value === "string") {
      const sanitized = sanitizePath(value);
      if (sanitized) bucket.push(sanitized);
      return;
    }
    if (value && typeof value === "object") {
      const ref = value as Record<string, unknown>;
      const candidates = [
        ref.path,
        ref.name,
        ref.description,
        ref.type,
      ]
        .map((entry) => (typeof entry === "string" ? entry : undefined))
        .filter((entry): entry is string => Boolean(entry));
      for (const candidate of candidates) {
        const sanitized = sanitizePath(candidate);
        if (sanitized) bucket.push(sanitized);
      }
    }
  };

  const collected: string[] = [];
  if (Array.isArray(feature.artifacts)) {
    for (const entry of feature.artifacts) {
      pushPaths(entry, collected);
    }
    return dedupe(collected);
  }

  for (const [key, value] of Object.entries(feature.artifacts)) {
    const sanitizedKey = sanitizePath(key);
    if (sanitizedKey) collected.push(sanitizedKey);
    pushPaths(value, collected);
  }

  return dedupe(collected);
};

const buildFeatureScopeEnv = (
  resolution: FeatureScopeResolution,
  graphPath: string,
): Record<string, string> => {
  const payload = {
    graphPath,
    features: resolution.features.map((feature) => ({
      id: feature.id,
      name: feature.name,
      status: feature.status,
    })),
    changedPaths: resolution.changedPaths,
    artifactPaths: resolution.artifactPaths,
    pathFilters: resolution.pathFilters,
  };

  return {
    [FEATURE_SCOPE_ENV_FLAG]: "1",
    OPEN_SWE_FEATURE_SCOPE: JSON.stringify(payload),
  };
};

const collectChangedPaths = async (
  sandbox: Sandbox,
  workingDir: string,
  scope: FeatureScopeOptions,
  timeoutSec?: number,
): Promise<string[]> => {
  const provided = scope.changedPaths?.map(normalizeRelativePath).filter(Boolean) ?? [];
  if (provided.length > 0) {
    return dedupe(provided);
  }

  if (!scope.baseBranch || !scope.headBranch) {
    return [];
  }

  try {
    const diff = await execInSandbox(
      sandbox,
      `git diff --name-only ${scope.baseBranch}...${scope.headBranch}`,
      { cwd: workingDir, timeoutSec },
    );

    if (diff.exitCode !== 0) {
      logger.warn("Failed to compute changed paths for feature scoping", {
        exitCode: diff.exitCode,
        stderr: diff.stderr?.slice(0, 200),
      });
      return [];
    }

    return dedupe(
      diff.stdout
        .split("\n")
        .map(normalizeRelativePath)
        .filter(Boolean),
    );
  } catch (error) {
    logger.warn("Unable to determine changed paths for feature scoping", { error });
    return [];
  }
};

const buildFeatureScopeCacheKey = (args: ExecOptions): string | undefined => {
  if (!args.featureScope) return undefined;
  const parts = [
    args.workingDir,
    args.featureScope.graphPath ?? "",
    args.featureScope.baseBranch ?? "",
    args.featureScope.headBranch ?? "",
    ...(args.featureScope.changedPaths ?? []),
  ];
  return parts.join("|");
};

const resolveFeatureScopeInternal = async (
  sandbox: Sandbox,
  args: ExecOptions,
): Promise<FeatureScopeResolution | null> => {
  const scope = args.featureScope;
  if (!scope) return null;
  if (!isFeatureScopeEnabled(scope)) return null;

  const graphPath = scope.graphPath ?? path.join(args.workingDir, DEFAULT_GRAPH_RELATIVE_PATH);

  let graphData;
  try {
    graphData = await loadFeatureGraph(graphPath);
  } catch (error) {
    logger.warn("Feature-scoped evaluation disabled: failed to load feature graph", {
      graphPath,
      error,
    });
    return null;
  }

  const graph = new FeatureGraph(graphData);
  const changedPaths = await collectChangedPaths(
    sandbox,
    args.workingDir,
    scope,
    args.timeoutSec,
  );
  const normalizedChanged = changedPaths.map(normalizeRelativePath).filter(Boolean);
  const artifactSeed = scope.artifactPaths?.map(normalizeRelativePath).filter(Boolean) ?? [];

  const impacted =
    normalizedChanged.length > 0
      ? impactedFeaturesByCodeChange(graph, normalizedChanged)
      : [];

  const artifactPaths = new Set<string>(artifactSeed);
  for (const feature of impacted) {
    for (const candidate of collectArtifactPaths(feature)) {
      const sanitized = sanitizePath(candidate);
      if (!sanitized) continue;
      artifactPaths.add(sanitized);
    }
  }

  const pythonChanges = normalizedChanged.filter(isLikelyPythonTarget);
  const candidatePaths = dedupe([
    ...pythonChanges,
    ...Array.from(artifactPaths).filter(isLikelyPythonTarget),
  ]).slice(0, MAX_PATH_FILTERS);

  const baseResolution: FeatureScopeResolution = {
    features: impacted,
    changedPaths: normalizedChanged,
    artifactPaths: Array.from(artifactPaths),
    pathFilters: candidatePaths,
    env: {},
  };

  const env = buildFeatureScopeEnv(baseResolution, graphPath);
  const resolution: FeatureScopeResolution = { ...baseResolution, env };

  if (isFeatureScopeDebug()) {
    logger.info("Feature scope resolution", {
      graphPath,
      changedCount: normalizedChanged.length,
      impactedFeatures: impacted.map((feature) => feature.id),
      pathFilters: candidatePaths,
    });
  }

  return resolution;
};

const getFeatureScopeResolution = async (
  sandbox: Sandbox,
  args: ExecOptions,
): Promise<FeatureScopeResolution | null> => {
  const key = buildFeatureScopeCacheKey(args);
  if (!key) return null;

  const cached = featureScopeCache.get(key);
  if (cached) {
    return await cached;
  }

  const pending = resolveFeatureScopeInternal(sandbox, args);
  featureScopeCache.set(key, pending);

  try {
    const resolved = await pending;
    if (resolved) {
      featureScopeCache.set(key, Promise.resolve(resolved));
    } else {
      featureScopeCache.delete(key);
    }
    return resolved;
  } catch (error) {
    featureScopeCache.delete(key);
    throw error;
  }
};

const executeWithFeatureScope = async (
  sandbox: Sandbox,
  args: ExecOptions,
  run: (command: string, env: Record<string, string> | undefined) => Promise<ExecResult>,
): Promise<{ result: ExecResult; scopeApplied: boolean }> => {
  const scope = await getFeatureScopeResolution(sandbox, args);
  const requireMatch = shouldRequireScopeMatch();

  let command = args.command;
  let scopeApplied = false;
  let mergedEnv = args.env;

  if (scope) {
    mergedEnv = mergeEnvironment(args.env, scope.env);
    if (scope.pathFilters.length > 0) {
      command = injectPathFiltersIntoCommand(args.command, scope.pathFilters);
      scopeApplied = true;
    } else if (requireMatch) {
      logger.info(
        "Feature-scoped evaluation requested but no matching paths were found; running default command",
      );
    }
  }

  const result = await run(command, mergedEnv);
  return { result, scopeApplied };
};

/**
 * Run ruff check and return score, error, and issues
 */
export const runRuffLint = async (
  sandbox: Sandbox,
  args: ExecOptions,
): Promise<RuffResult> => {
  logger.info("Running ruff check...");

  try {
    const { result: executionResult, scopeApplied } = await executeWithFeatureScope(
      sandbox,
      args,
      async (command, env) =>
        await execInSandbox(sandbox, command, {
          cwd: args.workingDir,
          env,
          timeoutSec: args.timeoutSec,
        }),
    );

    if (executionResult.exitCode === 0) {
      logger.info("Ruff analysis passed. No issues found.", {
        scopeApplied,
      });
      return {
        ruffScore: 1,
        error: null,
        issues: [],
      };
    }

    try {
      const issues: RuffIssue[] = JSON.parse(executionResult.result);
      const issueCount = Array.isArray(issues) ? issues.length : 0;
      const ruffScore = issueCount === 0 ? 1 : 0;

      logger.info(`Ruff found ${issueCount} issues`, {
        score: ruffScore,
        scopeApplied,
        issues: issues.slice(0, 3),
      });

      return {
        ruffScore,
        error: null,
        issues,
      };
    } catch (parseError) {
      logger.warn(
        "Could not parse ruff JSON output. Setting Ruff score to 0.",
        {
          parseError,
          scopeApplied,
          output: executionResult.result?.substring(0, 200) + "...",
        },
      );

      return {
        ruffScore: 0,
        error: parseError as Error,
        issues: [],
      };
    }
  } catch (error) {
    logger.error("Failed to run ruff check", { error });
    return {
      ruffScore: 0,
      error: error as Error,
      issues: [],
    };
  }
};

/**
 * Run mypy check and return score, error, and issues
 */
export const runMyPyTypeCheck = async (
  sandbox: Sandbox,
  args: ExecOptions,
): Promise<MyPyResult> => {
  logger.info("Running mypy check...");
  try {
    const { result: executionResult, scopeApplied } = await executeWithFeatureScope(
      sandbox,
      args,
      async (command, env) =>
        await execInSandbox(sandbox, command, {
          cwd: args.workingDir,
          env,
          timeoutSec: args.timeoutSec,
        }),
    );

    if (executionResult.exitCode === 0) {
      logger.info("Mypy analysis passed. No issues found.", {
        scopeApplied,
      });
      return {
        mypyScore: 1,
        error: null,
        issues: [],
      };
    }

    const errorLines = executionResult.result
      .split("\n")
      .filter((line) => line.includes(": error:") || line.includes(": warning:"));

    const issueCount = errorLines.length;
    const mypyScore = issueCount === 0 ? 1 : 0;

    logger.info(`Mypy found ${issueCount} issues`, {
      score: mypyScore,
      scopeApplied,
      issues: errorLines.slice(0, 3),
    });

    return {
      mypyScore,
      error: null,
      issues: errorLines,
    };
  } catch (error) {
    logger.error("Failed to run mypy check", { error });
    return {
      mypyScore: 0,
      error: error as Error,
      issues: [],
    };
  }
};
