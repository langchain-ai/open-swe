import { z } from "zod";

const metadataSchema = z.record(z.string(), z.unknown());

export const artifactRefSchema = z.union([
  z.string(),
  z
    .object({
      name: z.string().optional(),
      description: z.string().optional(),
      path: z.string().optional(),
      url: z.string().optional(),
      type: z.string().optional(),
      metadata: metadataSchema.optional(),
    })
    .strict(),
]);

export const artifactCollectionSchema = z.union([
  z.array(artifactRefSchema),
  z.record(z.string(), artifactRefSchema),
]);

export const featureNodeSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    description: z.string(),
    status: z.string(),
    group: z.string().optional(),
    metadata: metadataSchema.optional(),
    artifacts: artifactCollectionSchema.optional(),
  })
  .strict();

export const featureNodeSourceSchema = z
  .object({
    source: z.string(),
  })
  .strict();

export const featureNodeManifestSchema = z
  .object({
    manifest: z.string(),
  })
  .strict();

export const featureNodeEntrySchema = z.union([
  featureNodeSchema,
  featureNodeSourceSchema,
  featureNodeManifestSchema,
]);

export const featureEdgeSchema = z
  .object({
    source: z.string(),
    target: z.string(),
    type: z.string(),
    metadata: metadataSchema.optional(),
  })
  .strict();

export const featureEdgeSourceSchema = z
  .object({
    source: z.string(),
  })
  .strict();

export const featureEdgeManifestSchema = z
  .object({
    manifest: z.string(),
  })
  .strict();

export const featureEdgeEntrySchema = z.union([
  featureEdgeSchema,
  featureEdgeSourceSchema,
  featureEdgeManifestSchema,
]);

export const featureNodeManifestFileSchema = z
  .object({
    sources: z.array(
      z.union([
        z.string(),
        featureNodeSchema,
        featureNodeSourceSchema,
        featureNodeManifestSchema,
      ])
    ),
  })
  .strict();

export const featureEdgeManifestFileSchema = z
  .object({
    sources: z.array(
      z.union([
        z.string(),
        featureEdgeSchema,
        featureEdgeSourceSchema,
        featureEdgeManifestSchema,
      ])
    ),
  })
  .strict();

export const featureGraphFileSchema = z
  .object({
    version: z.number().int().positive(),
    nodes: z.array(featureNodeEntrySchema),
    edges: z.array(featureEdgeEntrySchema),
    artifacts: artifactCollectionSchema.optional(),
  })
  .strict();

export type ArtifactRef = z.infer<typeof artifactRefSchema>;
export type ArtifactCollection = z.infer<typeof artifactCollectionSchema>;
export type FeatureNode = z.infer<typeof featureNodeSchema>;
export type FeatureNodeEntry = z.infer<typeof featureNodeEntrySchema>;
export type FeatureEdge = z.infer<typeof featureEdgeSchema>;
export type FeatureEdgeEntry = z.infer<typeof featureEdgeEntrySchema>;
export type FeatureNodeManifestFile = z.infer<typeof featureNodeManifestFileSchema>;
export type FeatureEdgeManifestFile = z.infer<typeof featureEdgeManifestFileSchema>;
export type FeatureGraphFile = z.infer<typeof featureGraphFileSchema>;
