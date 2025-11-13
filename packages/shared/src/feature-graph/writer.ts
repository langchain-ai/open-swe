import { writeFile } from "node:fs/promises";
import path from "node:path";
import YAML from "yaml";

import {
  ArtifactCollection,
  FeatureEdge,
  FeatureEdgeEntry,
  FeatureGraphFile,
  FeatureNode,
  FeatureNodeEntry,
  artifactCollectionSchema,
  featureEdgeEntrySchema,
  featureGraphFileSchema,
  featureNodeEntrySchema,
} from "./types.js";

const KEY_PRIORITY = [
  "version",
  "id",
  "name",
  "description",
  "status",
  "group",
  "nodes",
  "edges",
  "artifacts",
  "source",
  "manifest",
  "target",
  "type",
  "metadata",
] as const;

type PlainObject = Record<string, unknown>;

type NodeEntryWithId = FeatureNode & { id: string };

type EdgeLike = FeatureEdge & { source: string; target: string; type: string };

const compareStrings = (a = "", b = "") => a.localeCompare(b);

const isPlainObject = (value: unknown): value is PlainObject =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isFeatureNode = (entry: FeatureNodeEntry): entry is FeatureNode =>
  Object.prototype.hasOwnProperty.call(entry, "id");

const isFeatureNodeSource = (
  entry: FeatureNodeEntry,
): entry is { source: string } =>
  Object.prototype.hasOwnProperty.call(entry, "source") &&
  !Object.prototype.hasOwnProperty.call(entry, "manifest");

const isFeatureNodeManifest = (
  entry: FeatureNodeEntry,
): entry is { manifest: string } =>
  Object.prototype.hasOwnProperty.call(entry, "manifest");

const isConcreteEdge = (entry: FeatureEdgeEntry): entry is EdgeLike =>
  Object.prototype.hasOwnProperty.call(entry, "target") &&
  Object.prototype.hasOwnProperty.call(entry, "type");

const isEdgeSourceReference = (
  entry: FeatureEdgeEntry,
): entry is { source: string } =>
  Object.prototype.hasOwnProperty.call(entry, "source") &&
  !Object.prototype.hasOwnProperty.call(entry, "target") &&
  !Object.prototype.hasOwnProperty.call(entry, "manifest");

const isEdgeManifestReference = (
  entry: FeatureEdgeEntry,
): entry is { manifest: string } =>
  Object.prototype.hasOwnProperty.call(entry, "manifest");

const getNodeEntryKey = (entry: FeatureNodeEntry): string => {
  if (isFeatureNodeManifest(entry)) {
    return `manifest:${entry.manifest}`;
  }

  if (isFeatureNodeSource(entry) && !isFeatureNode(entry)) {
    return `source:${entry.source}`;
  }

  if (isFeatureNode(entry)) {
    return `id:${entry.id}`;
  }

  throw new Error("Unsupported feature node entry encountered");
};

const getEdgeEntryKey = (entry: FeatureEdgeEntry): string => {
  if (isEdgeManifestReference(entry)) {
    return `manifest:${entry.manifest}`;
  }

  if (isEdgeSourceReference(entry)) {
    return `source:${entry.source}`;
  }

  if (isConcreteEdge(entry)) {
    return `edge:${entry.source}->${entry.target}#${entry.type}`;
  }

  throw new Error("Unsupported feature edge entry encountered");
};

const sortArray = (value: unknown[]): unknown[] => {
  const sorted = value.map((item) => sortValue(item));

  if (sorted.every((item): item is string => typeof item === "string")) {
    return [...sorted].sort(compareStrings);
  }

  if (sorted.every(isPlainObject)) {
    if (sorted.every((item) => Object.prototype.hasOwnProperty.call(item, "id"))) {
      return [...sorted].sort((a, b) =>
        compareStrings(String((a as NodeEntryWithId).id), String((b as NodeEntryWithId).id)),
      );
    }

    if (
      sorted.every(
        (item) =>
          Object.prototype.hasOwnProperty.call(item, "source") &&
          Object.prototype.hasOwnProperty.call(item, "target"),
      )
    ) {
      return [...sorted].sort((a, b) => {
        const sourceComparison = compareStrings(
          String((a as EdgeLike).source),
          String((b as EdgeLike).source),
        );
        if (sourceComparison !== 0) return sourceComparison;
        const targetComparison = compareStrings(
          String((a as EdgeLike).target),
          String((b as EdgeLike).target),
        );
        if (targetComparison !== 0) return targetComparison;
        return compareStrings(String((a as EdgeLike).type), String((b as EdgeLike).type));
      });
    }

    if (sorted.every((item) => Object.prototype.hasOwnProperty.call(item, "source"))) {
      return [...sorted].sort((a, b) =>
        compareStrings(
          String((a as { source: string }).source),
          String((b as { source: string }).source),
        ),
      );
    }

    if (sorted.every((item) => Object.prototype.hasOwnProperty.call(item, "manifest"))) {
      return [...sorted].sort((a, b) =>
        compareStrings(
          String((a as { manifest: string }).manifest),
          String((b as { manifest: string }).manifest),
        ),
      );
    }
  }

  return sorted;
};

const sortObject = (value: PlainObject): PlainObject =>
  Object.entries(value)
    .sort(([keyA], [keyB]) => {
      const indexA = KEY_PRIORITY.indexOf(keyA as (typeof KEY_PRIORITY)[number]);
      const indexB = KEY_PRIORITY.indexOf(keyB as (typeof KEY_PRIORITY)[number]);

      if (indexA !== -1 || indexB !== -1) {
        if (indexA === -1) return 1;
        if (indexB === -1) return -1;
        if (indexA !== indexB) return indexA - indexB;
      }

      return compareStrings(keyA, keyB);
    })
    .reduce<PlainObject>((acc, [key, entry]) => {
      acc[key] = sortValue(entry);
      return acc;
    }, {});

const sortValue = (value: unknown): unknown => {
  if (Array.isArray(value)) return sortArray(value);
  if (isPlainObject(value)) return sortObject(value);
  return value;
};

const formatFeatureGraphYaml = (graph: FeatureGraphFile): string => {
  const sortedGraph = sortValue(graph) as FeatureGraphFile;
  return YAML.stringify(sortedGraph, { indent: 2, lineWidth: 0 }).trimEnd() + "\n";
};

export const upsertFeatureNodeEntry = (
  entries: FeatureNodeEntry[],
  entry: FeatureNodeEntry,
): FeatureNodeEntry[] => {
  const normalized = featureNodeEntrySchema.parse(entry);
  const key = getNodeEntryKey(normalized);

  const next = entries.map((existing) => featureNodeEntrySchema.parse(existing));
  const index = next.findIndex((item) => getNodeEntryKey(item) === key);

  if (index === -1) {
    return [...next, normalized];
  }

  const updated = [...next];
  updated[index] = normalized;
  return updated;
};

export const upsertFeatureEdgeEntry = (
  entries: FeatureEdgeEntry[],
  entry: FeatureEdgeEntry,
): FeatureEdgeEntry[] => {
  const normalized = featureEdgeEntrySchema.parse(entry);
  const key = getEdgeEntryKey(normalized);

  const next = entries.map((existing) => featureEdgeEntrySchema.parse(existing));
  const index = next.findIndex((item) => getEdgeEntryKey(item) === key);

  if (index === -1) {
    return [...next, normalized];
  }

  const updated = [...next];
  updated[index] = normalized;
  return updated;
};

type WriteFeatureGraphOptions = {
  graphPath: string;
  version: number;
  nodes: FeatureNodeEntry[];
  edges: FeatureEdgeEntry[];
  artifacts?: ArtifactCollection;
};

export const writeFeatureGraphFile = async (
  options: WriteFeatureGraphOptions,
): Promise<void> => {
  const { graphPath, version, nodes, edges, artifacts } = options;

  const graph: FeatureGraphFile = featureGraphFileSchema.parse({
    version,
    nodes: nodes.map((entry) => featureNodeEntrySchema.parse(entry)),
    edges: edges.map((entry) => featureEdgeEntrySchema.parse(entry)),
    ...(artifacts
      ? { artifacts: artifactCollectionSchema.parse(artifacts) }
      : {}),
  });

  const formatted = formatFeatureGraphYaml(graph);
  const absolutePath = path.resolve(graphPath);
  await writeFile(absolutePath, formatted, "utf8");
};

export const renderFeatureGraphYaml = (graph: FeatureGraphFile): string =>
  formatFeatureGraphYaml(featureGraphFileSchema.parse(graph));
