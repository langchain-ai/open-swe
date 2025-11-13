import { readFile } from "node:fs/promises";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import { z } from "zod";
import {
  ArtifactCollection,
  FeatureEdge,
  FeatureEdgeEntry,
  FeatureEdgeManifestFile,
  FeatureGraphFile,
  FeatureNode,
  FeatureNodeEntry,
  FeatureNodeManifestFile,
  artifactCollectionSchema,
  featureEdgeEntrySchema,
  featureEdgeManifestFileSchema,
  featureEdgeSchema,
  featureGraphFileSchema,
  featureNodeEntrySchema,
  featureNodeManifestFileSchema,
  featureNodeSchema,
} from "./types.js";

export type FeatureGraphData = {
  version: number;
  nodes: Map<string, FeatureNode>;
  edges: FeatureEdge[];
  artifacts?: ArtifactCollection;
};

type Schema<T> = z.ZodType<T>;

const normalizeError = (error: unknown): Error => {
  if (error instanceof Error) return error;
  return new Error(String(error));
};

const parseYamlFile = async <T>({
  filePath,
  schema,
  descriptor,
}: {
  filePath: string;
  schema: Schema<T>;
  descriptor: string;
}): Promise<T> => {
  const absolutePath = path.resolve(filePath);

  let fileContent: string;
  try {
    fileContent = await readFile(absolutePath, "utf8");
  } catch (error) {
    throw new Error(
      `Failed to read ${descriptor} at ${absolutePath}: ${normalizeError(error).message}`
    );
  }

  let parsed: unknown;
  try {
    parsed = parseYaml(fileContent);
  } catch (error) {
    throw new Error(
      `Failed to parse YAML for ${descriptor} at ${absolutePath}: ${normalizeError(error).message}`
    );
  }

  const result = schema.safeParse(parsed);
  if (!result.success) {
    throw new Error(
      `Invalid ${descriptor} at ${absolutePath}: ${result.error.toString()}`
    );
  }

  return result.data;
};

const normalizeNodeManifestEntry = (
  entry: FeatureNodeManifestFile["sources"][number]
): FeatureNodeEntry => {
  if (typeof entry === "string") {
    return { source: entry };
  }

  if ("manifest" in entry) return entry;
  if ("source" in entry && !("target" in entry && "type" in entry)) {
    return entry;
  }

  if (featureNodeSchema.safeParse(entry).success) {
    return entry;
  }

  throw new Error("Unsupported entry encountered in feature node manifest");
};

const normalizeEdgeManifestEntry = (
  entry: FeatureEdgeManifestFile["sources"][number]
): FeatureEdgeEntry => {
  if (typeof entry === "string") {
    return { source: entry };
  }

  if ("manifest" in entry) return entry;
  if ("target" in entry && "type" in entry) return entry as FeatureEdgeEntry;
  if ("source" in entry && !("id" in entry)) return entry as FeatureEdgeEntry;

  if (featureEdgeSchema.safeParse(entry).success) {
    return entry;
  }

  throw new Error("Unsupported entry encountered in feature edge manifest");
};

const loadNodesFromEntries = async (
  entries: FeatureNodeEntry[],
  currentDir: string,
  visitedManifests: Set<string>
): Promise<FeatureNode[]> => {
  const nodes: FeatureNode[] = [];

  for (const entry of entries) {
    if ("manifest" in entry) {
      const manifestPath = path.resolve(currentDir, entry.manifest);

      if (visitedManifests.has(manifestPath)) {
        throw new Error(
          `Circular feature node manifest reference detected at ${manifestPath}`
        );
      }

      visitedManifests.add(manifestPath);
      const manifest = await parseYamlFile<FeatureNodeManifestFile>({
        filePath: manifestPath,
        schema: featureNodeManifestFileSchema,
        descriptor: "feature node manifest",
      });
      const manifestDir = path.dirname(manifestPath);
      const manifestEntries = manifest.sources.map(normalizeNodeManifestEntry);
      const nested = await loadNodesFromEntries(
        manifestEntries,
        manifestDir,
        visitedManifests
      );
      nodes.push(...nested);
      visitedManifests.delete(manifestPath);
      continue;
    }

    if ("source" in entry) {
      const nodePath = path.resolve(currentDir, entry.source);
      const node = await parseYamlFile<FeatureNode>({
        filePath: nodePath,
        schema: featureNodeSchema,
        descriptor: "feature node",
      });
      nodes.push(node);
      continue;
    }

    const result = featureNodeSchema.safeParse(entry);
    if (!result.success) {
      throw new Error(
        `Invalid inline feature node definition: ${result.error.toString()}`
      );
    }

    nodes.push(result.data);
  }

  return nodes;
};

const loadEdgesFromEntries = async (
  entries: FeatureEdgeEntry[],
  currentDir: string,
  visitedManifests: Set<string>
): Promise<FeatureEdge[]> => {
  const edges: FeatureEdge[] = [];

  for (const entry of entries) {
    if ("manifest" in entry) {
      const manifestPath = path.resolve(currentDir, entry.manifest);

      if (visitedManifests.has(manifestPath)) {
        throw new Error(
          `Circular feature edge manifest reference detected at ${manifestPath}`
        );
      }

      visitedManifests.add(manifestPath);
      const manifest = await parseYamlFile<FeatureEdgeManifestFile>({
        filePath: manifestPath,
        schema: featureEdgeManifestFileSchema,
        descriptor: "feature edge manifest",
      });
      const manifestDir = path.dirname(manifestPath);
      const manifestEntries = manifest.sources.map(normalizeEdgeManifestEntry);
      const nested = await loadEdgesFromEntries(
        manifestEntries,
        manifestDir,
        visitedManifests
      );
      edges.push(...nested);
      visitedManifests.delete(manifestPath);
      continue;
    }

    if ("source" in entry && !("target" in entry && "type" in entry)) {
      const edgePath = path.resolve(currentDir, entry.source);
      const edge = await parseYamlFile<FeatureEdge>({
        filePath: edgePath,
        schema: featureEdgeSchema,
        descriptor: "feature edge",
      });
      edges.push(edge);
      continue;
    }

    const result = featureEdgeSchema.safeParse(entry);
    if (!result.success) {
      throw new Error(
        `Invalid inline feature edge definition: ${result.error.toString()}`
      );
    }

    edges.push(result.data);
  }

  return edges;
};

export const loadFeatureGraph = async (
  graphPath: string
): Promise<FeatureGraphData> => {
  const absoluteGraphPath = path.resolve(graphPath);
  const graphDir = path.dirname(absoluteGraphPath);

  const envelope = await parseYamlFile<FeatureGraphFile>({
    filePath: absoluteGraphPath,
    schema: featureGraphFileSchema,
    descriptor: "feature graph",
  });

  const nodes = await loadNodesFromEntries(
    envelope.nodes.map((entry) => featureNodeEntrySchema.parse(entry)),
    graphDir,
    new Set<string>()
  );

  const edges = await loadEdgesFromEntries(
    envelope.edges.map((entry) => featureEdgeEntrySchema.parse(entry)),
    graphDir,
    new Set<string>()
  );

  const nodeMap = new Map<string, FeatureNode>();
  for (const node of nodes) {
    if (nodeMap.has(node.id)) {
      throw new Error(`Duplicate feature node id detected: ${node.id}`);
    }

    nodeMap.set(node.id, node);
  }

  const artifacts = envelope.artifacts
    ? artifactCollectionSchema.parse(envelope.artifacts)
    : undefined;

  return {
    version: envelope.version,
    nodes: nodeMap,
    edges,
    artifacts,
  };
};
