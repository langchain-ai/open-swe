/**
 * @module feature-graph/mappings
 *
 * Utilities for relating repository artifacts (tests, docs, code paths) to
 * feature nodes stored in the {@link FeatureGraph}. The helpers surface likely
 * associations using layered heuristics:
 *
 * - Direct references declared on feature nodes via their `artifacts`
 *   property.
 * - Optional plan metadata that maps features and artifacts to additional
 *   keywords or resource paths. The helpers accept loosely typed metadata and
 *   try to recover useful hints regardless of the exact shape.
 * - Task plans and user requests captured in {@link TaskPlan}. We reuse the
 *   existing `getActiveTask`/`getActivePlanItems` helpers to gather plan
 *   summaries and use them as extra signals when no direct artifact matches are
 *   present.
 *
 * Because feature graphs, plan metadata, and task plans are authored by
 * humans, the information may be incomplete. All helpers return best-effort
 * results and fall back to partial matches whenever the available signals
 * disagree. Callers should treat empty arrays as "no confident match" rather
 * than a hard failure and may choose to merge these results with
 * domain-specific context.
 */

import { FeatureGraph } from "./graph.js";
import {
  ArtifactCollection,
  ArtifactRef,
  FeatureNode,
} from "./types.js";
import { Task, TaskPlan } from "../open-swe/types.js";
import {
  getActivePlanItems,
  getActiveTask,
} from "../open-swe/tasks.js";

type ArtifactEntry = {
  ref: ArtifactRef;
  key?: string;
};

type NormalizedValue = {
  value: string;
  normalized: string;
};

type NormalizedFeaturePlanHints = {
  keywords: NormalizedValue[];
  artifacts: NormalizedValue[];
  tests: NormalizedValue[];
};

type NormalizedArtifactPlanHints = {
  features: string[];
  keywords: NormalizedValue[];
  tests: NormalizedValue[];
};

const TEST_KEYWORDS = new Set([
  "test",
  "tests",
  "spec",
  "specs",
  "integration",
  "e2e",
  "unit",
  "qa",
  "cypress",
]);

const TEST_PATH_FRAGMENTS = [
  ".test.",
  ".tests.",
  ".spec.",
  "/test/",
  "/tests/",
  "__tests__",
  "test/",
  "tests/",
  "/__tests__/",
];

const GENERIC_KEYWORDS = new Set([
  "feature",
  "features",
  "test",
  "tests",
  "spec",
  "specs",
  "doc",
  "docs",
  "app",
  "apps",
  "src",
  "path",
]);

const PATH_CANDIDATE_REGEX = /[A-Za-z0-9_./\\-]+(?:\.[A-Za-z0-9_]+)+/g;

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const normalize = (value: string): string =>
  value
    .toLowerCase()
    .replace(/\\+/g, "/")
    .replace(/\s+/g, " ")
    .trim();

const tokenize = (value: string): string[] => {
  const tokens = value.match(/[a-z0-9]+/g);
  if (!tokens) return [];
  return Array.from(new Set(tokens));
};

const normalizeToSet = (values: Iterable<string>): Set<string> => {
  const set = new Set<string>();
  for (const value of values) {
    if (!value) continue;
    const normalized = normalize(value);
    if (normalized) set.add(normalized);
  }
  return set;
};

const dedupeNormalizedValues = (
  values: NormalizedValue[],
): NormalizedValue[] => {
  const map = new Map<string, NormalizedValue>();
  for (const value of values) {
    if (!value.normalized) continue;
    if (!map.has(value.normalized)) {
      map.set(value.normalized, value);
    }
  }
  return Array.from(map.values());
};

const createNormalizedValue = (value?: string): NormalizedValue | undefined => {
  if (!value) return undefined;
  const normalized = normalize(value);
  if (!normalized) return undefined;
  return { value: value.trim(), normalized };
};

const collectStrings = (value: unknown): string[] => {
  if (typeof value === "string") return [value];
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectStrings(item));
  }
  if (isPlainObject(value)) {
    return Object.values(value).flatMap((item) => collectStrings(item));
  }
  return [];
};

const collectStringsNormalized = (value: unknown): NormalizedValue[] =>
  dedupeNormalizedValues(
    collectStrings(value)
      .map((str) => createNormalizedValue(str))
      .filter((entry): entry is NormalizedValue => Boolean(entry)),
  );

const artifactRefValues = (ref: ArtifactRef): NormalizedValue[] => {
  if (typeof ref === "string") {
    const entry = createNormalizedValue(ref);
    return entry ? [entry] : [];
  }

  const values: NormalizedValue[] = [];
  const push = (candidate?: string) => {
    const entry = createNormalizedValue(candidate);
    if (entry) values.push(entry);
  };

  push(ref.path);
  push(ref.url);
  push(ref.name);
  push(ref.description);
  push(ref.type);

  if (ref.metadata) {
    values.push(...collectStringsNormalized(ref.metadata));
  }

  return dedupeNormalizedValues(values);
};

const flattenArtifactCollection = (
  artifacts: ArtifactCollection | undefined,
): ArtifactEntry[] => {
  if (!artifacts) return [];
  if (Array.isArray(artifacts)) {
    return artifacts.map((ref) => ({ ref }));
  }

  return Object.entries(artifacts).map(([key, ref]) => ({
    key,
    ref,
  }));
};

const matchNormalizedStrings = (
  candidates: Iterable<string>,
  normalizedQuery: string,
  queryTokens: string[],
): boolean => {
  const filteredTokens = queryTokens.filter(
    (token) => token && token.length >= 3 && !GENERIC_KEYWORDS.has(token),
  );

  if (!normalizedQuery && filteredTokens.length === 0) return false;

  for (const candidate of candidates) {
    if (!candidate) continue;
    if (normalizedQuery && candidate === normalizedQuery) return true;
    if (normalizedQuery) {
      if (candidate.includes(normalizedQuery) || normalizedQuery.includes(candidate)) {
        return true;
      }
    }

    for (const token of filteredTokens) {
      if (!token) continue;
      if (candidate.includes(token) || token.includes(candidate)) {
        return true;
      }
    }
  }

  return false;
};

const matchNormalizedValues = (
  values: NormalizedValue[],
  normalizedQuery: string,
  queryTokens: string[],
): boolean =>
  matchNormalizedStrings(
    values.map((value) => value.normalized),
    normalizedQuery,
    queryTokens,
  );

const isTestLike = (normalizedValue: string): boolean => {
  if (!normalizedValue) return false;
  if (TEST_PATH_FRAGMENTS.some((fragment) => normalizedValue.includes(fragment))) {
    return true;
  }

  return tokenize(normalizedValue).some((token) => TEST_KEYWORDS.has(token));
};

const extractTestPathsFromText = (text: string): NormalizedValue[] => {
  const matches = text.match(PATH_CANDIDATE_REGEX);
  if (!matches) return [];

  const values: NormalizedValue[] = [];
  for (const match of matches) {
    const entry = createNormalizedValue(match);
    if (!entry) continue;
    if (isTestLike(entry.normalized)) {
      values.push(entry);
    }
  }

  return dedupeNormalizedValues(values);
};

const collectFeatureTestValues = (
  feature: FeatureNode,
  planHints?: NormalizedFeaturePlanHints,
): NormalizedValue[] => {
  const values: NormalizedValue[] = [];

  const artifactEntries = flattenArtifactCollection(feature.artifacts);
  for (const entry of artifactEntries) {
    const artifactValues = artifactRefValues(entry.ref);
    if (entry.key) {
      const keyEntry = createNormalizedValue(entry.key);
      if (keyEntry) artifactValues.push(keyEntry);
    }

    for (const value of artifactValues) {
      if (isTestLike(value.normalized)) {
        values.push(value);
      }
    }
  }

  if (planHints) {
    for (const candidate of planHints.tests) {
      values.push(candidate);
    }
    for (const candidate of planHints.artifacts) {
      if (isTestLike(candidate.normalized)) values.push(candidate);
    }
    for (const candidate of planHints.keywords) {
      if (isTestLike(candidate.normalized)) values.push(candidate);
    }
  }

  return dedupeNormalizedValues(values);
};

const artifactRefIdentifier = (ref: ArtifactRef, key?: string): string => {
  if (typeof ref === "string") return normalize(ref);

  const candidates = [ref.path, ref.url, ref.name, key, ref.description, ref.type];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const normalized = normalize(candidate);
    if (normalized) return normalized;
  }

  if (ref.metadata) {
    const strings = collectStringsNormalized(ref.metadata);
    if (strings.length > 0) return strings[0].normalized;
  }

  return normalize(JSON.stringify(ref));
};

const collectArtifactValues = (feature: FeatureNode): NormalizedValue[] => {
  const values: NormalizedValue[] = [];
  for (const entry of flattenArtifactCollection(feature.artifacts)) {
    values.push(...artifactRefValues(entry.ref));
    if (entry.key) {
      const keyEntry = createNormalizedValue(entry.key);
      if (keyEntry) values.push(keyEntry);
    }
  }

  return dedupeNormalizedValues(values);
};

const collectFeatureKeywords = (feature: FeatureNode): string[] => {
  const keywords = new Set<string>();

  const addToken = (token: string) => {
    if (!token) return;
    if (token.length < 3) return;
    if (GENERIC_KEYWORDS.has(token)) return;
    keywords.add(token);
  };

  const pushString = (value?: string, includeFull = true) => {
    if (!value) return;
    const normalized = normalize(value);
    if (!normalized) return;
    if (includeFull) keywords.add(normalized);
    for (const token of tokenize(normalized)) {
      addToken(token);
    }
  };

  pushString(feature.id);
  pushString(feature.name);
  pushString(feature.group);
  pushString(feature.description, false);

  const metadataValues = collectStringsNormalized(feature.metadata);
  for (const entry of metadataValues) {
    pushString(entry.value, false);
  }

  return Array.from(keywords).filter(Boolean);
};

const collectTaskTextRaw = (task: Task): string[] => {
  const values: string[] = [task.request, task.title ?? "", task.summary ?? ""];

  for (const revision of task.planRevisions) {
    for (const plan of revision.plans) {
      values.push(plan.plan, plan.summary ?? "");
    }
  }

  return values.filter(Boolean);
};

const collectTaskTextNormalized = (task: Task): string[] =>
  Array.from(normalizeToSet(collectTaskTextRaw(task)));

const gatherTaskTestValues = (task: Task): NormalizedValue[] => {
  const values: NormalizedValue[] = [];
  for (const text of collectTaskTextRaw(task)) {
    values.push(...extractTestPathsFromText(text));
  }
  return dedupeNormalizedValues(values);
};

const taskRelatesToFeature = (
  task: Task,
  featureKeywords: string[],
): boolean => {
  if (featureKeywords.length === 0) return false;
  const taskStrings = collectTaskTextNormalized(task);
  if (taskStrings.length === 0) return false;

  return featureKeywords.some((keyword) =>
    taskStrings.some(
      (text) => keyword && (text.includes(keyword) || keyword.includes(text)),
    ),
  );
};

const mapTasksToFeatures = (
  tasks: Task[],
  features: FeatureNode[],
): Map<string, Task[]> => {
  const mapping = new Map<string, Task[]>();

  if (tasks.length === 0) return mapping;

  for (const feature of features) {
    const keywords = collectFeatureKeywords(feature);
    const related = tasks.filter((task) => taskRelatesToFeature(task, keywords));
    if (related.length > 0) {
      mapping.set(feature.id, related);
    }
  }

  return mapping;
};

const resolveTasks = (
  taskPlan?: TaskPlan,
  taskId?: string,
): Task[] => {
  if (!taskPlan) return [];

  if (taskId) {
    const task = taskPlan.tasks.find((candidate) => candidate.id === taskId);
    return task ? [task] : [];
  }

  try {
    return [getActiveTask(taskPlan)];
  } catch (_error) {
    return [...taskPlan.tasks];
  }
};

const collectTaskContext = (
  taskPlan?: TaskPlan,
  taskId?: string,
): { tasks: Task[]; keywords: string[] } => {
  if (!taskPlan) return { tasks: [], keywords: [] };

  const tasks = resolveTasks(taskPlan, taskId);
  const keywords = new Set<string>();

  if (!taskId) {
    try {
      const activeItems = getActivePlanItems(taskPlan);
      for (const item of activeItems) {
        if (item.plan) keywords.add(normalize(item.plan));
        if (item.summary) keywords.add(normalize(item.summary));
      }
    } catch (_error) {
      // Ignore errors from missing active tasks – the task list fallback is
      // still useful for heuristics.
    }
  }

  for (const task of tasks) {
    for (const text of collectTaskTextNormalized(task)) {
      keywords.add(text);
    }
  }

  return { tasks, keywords: Array.from(keywords).filter(Boolean) };
};

const isTestArtifactEntry = (entry: ArtifactEntry): boolean => {
  const values = artifactRefValues(entry.ref);
  if (entry.key) {
    const keyValue = createNormalizedValue(entry.key);
    if (keyValue) values.push(keyValue);
  }
  return values.some((value) => isTestLike(value.normalized));
};

const matchNormalizedValuesAgainstQuery = (
  values: NormalizedValue[],
  normalizedQuery: string,
  queryTokens: string[],
) =>
  matchNormalizedStrings(
    values.map((value) => value.normalized),
    normalizedQuery,
    queryTokens,
  );

const scoreFeature = (
  feature: FeatureNode,
  context: MappingContext,
  treatAsTest: boolean,
): number => {
  const {
    normalizedQuery,
    queryTokens,
    planFeatureHints,
    directPlanMatches,
    tasksByFeature,
    contextKeywords,
  } = context;

  let score = 0;

  const artifactValues = collectArtifactValues(feature);
  const artifactStrings = artifactValues.map((value) => value.normalized);

  if (normalizedQuery && artifactStrings.includes(normalizedQuery)) {
    score += 8;
  } else if (
    normalizedQuery &&
    artifactStrings.some((candidate) => candidate.includes(normalizedQuery))
  ) {
    score += 5;
  }

  if (matchNormalizedStrings(artifactStrings, normalizedQuery, queryTokens)) {
    score += 2;
  }

  const featureKeywords = collectFeatureKeywords(feature);
  if (matchNormalizedStrings(featureKeywords, normalizedQuery, queryTokens)) {
    score += 3;
  }

  const planHints = planFeatureHints.get(feature.id);
  if (planHints) {
    if (matchNormalizedValues(planHints.artifacts, normalizedQuery, queryTokens)) {
      score += 4;
    }
    if (matchNormalizedValues(planHints.keywords, normalizedQuery, queryTokens)) {
      score += 2;
    }
    if (
      treatAsTest &&
      matchNormalizedValues(planHints.tests, normalizedQuery, queryTokens)
    ) {
      score += 5;
    }
  }

  if (directPlanMatches.has(feature.id)) {
    score += 6;
  }

  if (
    contextKeywords.length > 0 &&
    featureKeywords.some((keyword) =>
      contextKeywords.some(
        (text) => keyword && (text.includes(keyword) || keyword.includes(text)),
      ),
    )
  ) {
    score += 1;
  }

  const relatedTasks = tasksByFeature.get(feature.id);
  if (relatedTasks && relatedTasks.length > 0) {
    const taskStrings = relatedTasks.flatMap((task) =>
      collectTaskTextNormalized(task),
    );

    if (matchNormalizedStrings(taskStrings, normalizedQuery, queryTokens)) {
      score += 3;
    }

    if (treatAsTest) {
      const taskTestValues = relatedTasks.flatMap((task) =>
        gatherTaskTestValues(task),
      );
      if (
        matchNormalizedValuesAgainstQuery(
          taskTestValues,
          normalizedQuery,
          queryTokens,
        )
      ) {
        score += 4;
      }
    }
  }

  if (treatAsTest) {
    const testValues = collectFeatureTestValues(feature, planHints);
    if (testValues.some((value) => value.normalized === normalizedQuery)) {
      score += 6;
    } else if (
      matchNormalizedValuesAgainstQuery(
        testValues,
        normalizedQuery,
        queryTokens,
      )
    ) {
      score += 4;
    }
  }

  return score;
};

const ensureMinimumScore = (
  score: number,
  featureId: string,
  directMatches: Set<string>,
  minimum: number,
): number => {
  if (score > 0) return score;
  return directMatches.has(featureId) ? minimum : 0;
};

type MappingContext = {
  normalizedQuery: string;
  queryTokens: string[];
  planFeatureHints: Map<string, NormalizedFeaturePlanHints>;
  directPlanMatches: Set<string>;
  tasksByFeature: Map<string, Task[]>;
  contextKeywords: string[];
};

export type PlanMetadataFeatureHints =
  | string
  | string[]
  | {
      keywords?: string | string[];
      artifacts?: string | string[];
      tests?: string | string[];
    };

export type PlanMetadataArtifactHints =
  | string
  | string[]
  | {
      features?: string | string[];
      keywords?: string | string[];
      tests?: string | string[];
    };

export interface PlanMetadata {
  features?: Record<string, PlanMetadataFeatureHints>;
  artifacts?: Record<string, PlanMetadataArtifactHints>;
  [key: string]:
    | PlanMetadataFeatureHints
    | PlanMetadataArtifactHints
    | Record<string, PlanMetadataFeatureHints>
    | Record<string, PlanMetadataArtifactHints>
    | undefined;
}

const toArray = (value?: string | string[]): string[] => {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
};

const normalizeFeaturePlanHints = (
  value: PlanMetadataFeatureHints | undefined,
): NormalizedFeaturePlanHints => {
  const keywords: NormalizedValue[] = [];
  const artifacts: NormalizedValue[] = [];
  const tests: NormalizedValue[] = [];

  const pushMany = (entries: string[], target: NormalizedValue[]) => {
    for (const entry of entries) {
      const normalized = createNormalizedValue(entry);
      if (normalized) target.push(normalized);
    }
  };

  if (!value) {
    return { keywords, artifacts, tests };
  }

  if (typeof value === "string" || Array.isArray(value)) {
    pushMany(toArray(value), keywords);
  } else {
    pushMany(toArray(value.keywords), keywords);
    pushMany(toArray(value.artifacts), artifacts);
    pushMany(toArray(value.tests), tests);
    pushMany(collectStrings(value), keywords);
  }

  for (const candidate of [...keywords, ...artifacts]) {
    if (isTestLike(candidate.normalized)) {
      tests.push(candidate);
    }
  }

  return {
    keywords: dedupeNormalizedValues(keywords),
    artifacts: dedupeNormalizedValues(artifacts),
    tests: dedupeNormalizedValues(tests),
  };
};

const normalizeArtifactPlanHints = (
  value: PlanMetadataArtifactHints | undefined,
): NormalizedArtifactPlanHints => {
  const features = new Set<string>();
  const keywords: NormalizedValue[] = [];
  const tests: NormalizedValue[] = [];

  const pushMany = (entries: string[], target: NormalizedValue[]) => {
    for (const entry of entries) {
      const normalized = createNormalizedValue(entry);
      if (normalized) target.push(normalized);
    }
  };

  if (!value) {
    return { features: [], keywords, tests };
  }

  if (typeof value === "string" || Array.isArray(value)) {
    pushMany(toArray(value), keywords);
  } else {
    for (const feature of toArray(value.features)) {
      const normalized = normalize(feature);
      if (normalized) features.add(normalized);
    }
    pushMany(toArray(value.keywords), keywords);
    pushMany(toArray(value.tests), tests);
    pushMany(collectStrings(value), keywords);
  }

  for (const candidate of keywords) {
    if (isTestLike(candidate.normalized)) {
      tests.push(candidate);
    }
  }

  return {
    features: Array.from(features),
    keywords: dedupeNormalizedValues(keywords),
    tests: dedupeNormalizedValues(tests),
  };
};

const indexPlanMetadata = (
  metadata?: PlanMetadata,
): {
  featureHints: Map<string, NormalizedFeaturePlanHints>;
  artifactHints: Map<string, NormalizedArtifactPlanHints>;
} => {
  const featureHints = new Map<string, NormalizedFeaturePlanHints>();
  const artifactHints = new Map<string, NormalizedArtifactPlanHints>();

  if (!metadata) {
    return { featureHints, artifactHints };
  }

  if (metadata.features && isPlainObject(metadata.features)) {
    for (const [featureId, hints] of Object.entries(metadata.features)) {
      featureHints.set(normalize(featureId), normalizeFeaturePlanHints(hints));
    }
  }

  if (metadata.artifacts && isPlainObject(metadata.artifacts)) {
    for (const [artifactId, hints] of Object.entries(metadata.artifacts)) {
      artifactHints.set(
        normalize(artifactId),
        normalizeArtifactPlanHints(hints),
      );
    }
  }

  for (const [key, hints] of Object.entries(metadata)) {
    if (key === "features" || key === "artifacts") continue;
    if (hints === undefined) continue;
    featureHints.set(normalize(key), normalizeFeaturePlanHints(hints));
  }

  return { featureHints, artifactHints };
};

const buildMappingContext = (
  query: string,
  options: FeatureMappingOptions | undefined,
  features: FeatureNode[],
  treatAsTest: boolean,
): MappingContext => {
  const normalizedQuery = normalize(query);
  const queryTokens = tokenize(normalizedQuery);

  const featureIdLookup = new Map<string, FeatureNode>();
  for (const feature of features) {
    featureIdLookup.set(normalize(feature.id), feature);
  }

  const { featureHints: rawFeatureHints, artifactHints } = indexPlanMetadata(
    options?.planMetadata,
  );

  const planFeatureHints = new Map<string, NormalizedFeaturePlanHints>();
  for (const [normalizedId, hints] of rawFeatureHints.entries()) {
    const feature = featureIdLookup.get(normalizedId);
    if (feature) {
      planFeatureHints.set(feature.id, hints);
    }
  }

  const directPlanMatches = new Set<string>();
  for (const [artifactKey, hints] of artifactHints.entries()) {
    const candidates = [artifactKey, ...hints.keywords.map((value) => value.normalized)];
    if (treatAsTest) {
      candidates.push(...hints.tests.map((value) => value.normalized));
    }

    if (matchNormalizedStrings(candidates, normalizedQuery, queryTokens)) {
      for (const featureId of hints.features) {
        const feature = featureIdLookup.get(featureId);
        if (feature) directPlanMatches.add(feature.id);
      }
    }
  }

  const { tasks, keywords } = collectTaskContext(
    options?.taskPlan,
    options?.taskId,
  );
  const tasksByFeature = mapTasksToFeatures(tasks, features);

  const contextKeywords = new Set<string>(keywords);
  if (options?.userRequests) {
    const requests = Array.isArray(options.userRequests)
      ? options.userRequests
      : [options.userRequests];
    for (const request of requests) {
      const normalized = normalize(request);
      if (normalized) contextKeywords.add(normalized);
    }
  }

  return {
    normalizedQuery,
    queryTokens,
    planFeatureHints,
    directPlanMatches,
    tasksByFeature,
    contextKeywords: Array.from(contextKeywords).filter(Boolean),
  };
};

export type FeatureMappingOptions = {
  taskPlan?: TaskPlan;
  taskId?: string;
  planMetadata?: PlanMetadata;
  userRequests?: string | string[];
};

export const featuresForArtifact = (
  graph: FeatureGraph,
  artifactQuery: string,
  options?: FeatureMappingOptions,
): FeatureNode[] => {
  const normalizedQuery = normalize(artifactQuery);
  if (!normalizedQuery) return [];

  const features = graph.listFeatures();
  const context = buildMappingContext(
    artifactQuery,
    options,
    features,
    false,
  );

  const scored: { feature: FeatureNode; score: number }[] = [];

  for (const feature of features) {
    const score = ensureMinimumScore(
      scoreFeature(feature, context, false),
      feature.id,
      context.directPlanMatches,
      2,
    );

    if (score > 0) {
      scored.push({ feature, score });
    }
  }

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.feature.name.localeCompare(b.feature.name);
  });

  return scored.map((entry) => entry.feature);
};

export const featuresForTest = (
  graph: FeatureGraph,
  testPath: string,
  options?: FeatureMappingOptions,
): FeatureNode[] => {
  const normalizedQuery = normalize(testPath);
  if (!normalizedQuery) return [];

  const features = graph.listFeatures();
  const context = buildMappingContext(testPath, options, features, true);
  const scored: { feature: FeatureNode; score: number }[] = [];

  for (const feature of features) {
    const score = ensureMinimumScore(
      scoreFeature(feature, context, true),
      feature.id,
      context.directPlanMatches,
      4,
    );

    if (score > 0) {
      scored.push({ feature, score });
    }
  }

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.feature.name.localeCompare(b.feature.name);
  });

  return scored.map((entry) => entry.feature);
};

const getPlanHintsForFeature = (
  feature: FeatureNode,
  metadata?: PlanMetadata,
): NormalizedFeaturePlanHints | undefined => {
  const { featureHints } = indexPlanMetadata(metadata);
  return featureHints.get(normalize(feature.id));
};

export const testsForFeature = (
  graph: FeatureGraph,
  featureId: string,
  options?: FeatureMappingOptions,
): ArtifactRef[] => {
  const feature = graph.getFeature(featureId);
  if (!feature) return [];

  const planHints = getPlanHintsForFeature(feature, options?.planMetadata);
  const artifactEntries = flattenArtifactCollection(feature.artifacts);
  const results = new Map<string, ArtifactRef>();

  for (const entry of artifactEntries) {
    if (!isTestArtifactEntry(entry)) continue;
    const identifier = artifactRefIdentifier(entry.ref, entry.key);
    results.set(identifier, entry.ref);
  }

  if (planHints) {
    for (const candidate of collectFeatureTestValues(feature, planHints)) {
      const artifact: ArtifactRef = { path: candidate.value };
      results.set(candidate.normalized, artifact);
    }
  }

  if (options?.taskPlan) {
    const { tasks } = collectTaskContext(options.taskPlan, options.taskId);
    const relatedTasks = mapTasksToFeatures(tasks, [feature]).get(feature.id) ?? [];
    const fallbackTasks = relatedTasks.length > 0 ? relatedTasks : tasks;

    for (const task of fallbackTasks) {
      for (const candidate of gatherTaskTestValues(task)) {
        const artifact: ArtifactRef = { path: candidate.value };
        results.set(candidate.normalized, artifact);
      }
    }
  }

  return Array.from(results.values());
};

export const featuresForArtifactOrTest = (
  graph: FeatureGraph,
  identifier: string,
  options?: FeatureMappingOptions,
): FeatureNode[] => {
  const testMatches = featuresForTest(graph, identifier, options);
  if (testMatches.length > 0) {
    return testMatches;
  }
  return featuresForArtifact(graph, identifier, options);
};

export const featuresForArtifactRef = (
  graph: FeatureGraph,
  artifact: ArtifactRef,
  options?: FeatureMappingOptions,
): FeatureNode[] => {
  if (typeof artifact === "string") {
    return featuresForArtifactOrTest(graph, artifact, options);
  }

  const candidates = [
    artifact.path,
    artifact.url,
    artifact.name,
    artifact.description,
    artifact.type,
  ].filter((value): value is string => Boolean(value));

  for (const candidate of candidates) {
    const matches = featuresForArtifactOrTest(graph, candidate, options);
    if (matches.length > 0) {
      return matches;
    }
  }

  return [];
};

export const featuresForArtifactCollection = (
  graph: FeatureGraph,
  collection: ArtifactCollection | undefined,
  options?: FeatureMappingOptions,
): FeatureNode[] => {
  if (!collection) return [];
  const seen = new Map<string, { feature: FeatureNode; score: number }>();

  for (const entry of flattenArtifactCollection(collection)) {
    const matches = featuresForArtifactRef(graph, entry.ref, options);
    matches.forEach((feature, index) => {
      const weight = matches.length - index;
      const existing = seen.get(feature.id);
      if (existing) {
        existing.score += weight;
      } else {
        seen.set(feature.id, { feature, score: weight });
      }
    });
  }

  const ranked = Array.from(seen.values());
  ranked.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.feature.name.localeCompare(b.feature.name);
  });

  return ranked.map((entry) => entry.feature);
};

export const impactedFeaturesByCodeChange = (
  graph: FeatureGraph,
  changedPaths: Iterable<string>,
  options?: FeatureMappingOptions,
): FeatureNode[] => {
  const aggregate = new Map<string, { feature: FeatureNode; score: number }>();
  const uniquePaths: string[] = [];
  const seen = new Set<string>();

  for (const rawPath of changedPaths) {
    const normalized = normalize(rawPath);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    uniquePaths.push(rawPath);
  }

  for (const path of uniquePaths) {
    const artifactMatches = featuresForArtifact(graph, path, options);
    artifactMatches.forEach((feature, index) => {
      const weight = Math.max(artifactMatches.length - index, 1);
      const existing = aggregate.get(feature.id);
      if (existing) {
        existing.score += weight;
      } else {
        aggregate.set(feature.id, { feature, score: weight });
      }
    });

    const testMatches = featuresForTest(graph, path, options);
    testMatches.forEach((feature, index) => {
      const weight = Math.max(testMatches.length - index, 1);
      const existing = aggregate.get(feature.id);
      if (existing) {
        existing.score += weight;
      } else {
        aggregate.set(feature.id, { feature, score: weight });
      }
    });
  }

  const ranked = Array.from(aggregate.values());
  ranked.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    return a.feature.name.localeCompare(b.feature.name);
  });

  return ranked.map((entry) => entry.feature);
};

