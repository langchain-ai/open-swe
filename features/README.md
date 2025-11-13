# Feature Graph

The feature graph captures relationships between individual feature initiatives and any cross-cutting dependencies. All graph configuration lives under `features/` at the repository root.

## Directory structure

- `graph/graph.yaml` – entry point that describes the overall graph.
- `graph/*.manifest.yaml` – optional manifest files that expand into additional node or edge sources.
- `nodes/<feature-id>.yaml` – optional per-feature definitions that can be referenced by the graph or a manifest.
- `artifacts/` – optional supporting documents that individual features or the graph can link to.

## `graph.yaml`

`graph.yaml` is the canonical description of the feature graph. It must contain:

- `version` – schema version number used by tooling.
- `nodes` – array of feature node entries or references.
- `edges` – array describing dependencies between nodes.
- `artifacts` *(optional)* – array or map of graph-level resources.

### Node entries

Each entry within `nodes` may be either:

1. An inline [`FeatureNode`](#featurenode) definition.
2. An object with a `source` key pointing to a file that contains a `FeatureNode` payload (for example `../nodes/sample-feature.yaml`).
3. An object with a `manifest` key pointing to a manifest file. Manifest files contain a `sources` array and each item in the array must be either a relative path to a `FeatureNode` file or an inline `FeatureNode` object. Tooling is expected to inline the manifest entries in the order provided.

This structure allows you to keep small features inline in `graph.yaml` while larger features can live in their own files.

### Edge entries

Each entry within `edges` must be a [`FeatureEdge`](#featureedge). Edges may also be declared via manifest files using the same `manifest` mechanism described above for nodes.

### Artifacts

The `artifacts` section is optional and can be omitted. When present, it may be either:

- An array of artifact descriptors (for example, strings or objects with `name`/`path`).
- A mapping from artifact names to resource metadata.

The formatting helper described below will keep artifact definitions sorted for stability, regardless of which representation you choose.

## `FeatureNode`

A feature node describes an individual initiative. The expected keys are:

- `id` – unique identifier for the feature.
- `name` – human-readable title.
- `description` – concise explanation of the feature.
- `status` – lifecycle state (for example `proposed`, `in-progress`, `complete`).
- `group` *(optional)* – logical grouping such as a product area, team, or milestone.
- `metadata` *(optional)* – arbitrary key/value data such as owners, tags, or links. Nested structures are allowed.
- `artifacts` *(optional)* – map or list of resources that are specific to this feature.

## `FeatureEdge`

Edges express relationships between features. The expected keys are:

- `source` – `id` of the upstream `FeatureNode`.
- `target` – `id` of the downstream `FeatureNode`.
- `type` – relationship classification such as `blocks`, `relates-to`, or `duplicate-of`.
- `metadata` *(optional)* – additional data describing the relationship. This may include notes, owners, or timestamps.

## Formatting helper

Use the formatting helper to ensure consistent ordering of keys and array entries when editing YAML files under `features/`.

```bash
yarn format:features [<path> ...]
```

If no paths are provided, the helper formats `features/graph/graph.yaml` by default. You can pass additional file paths (manifests, nodes, etc.) and the helper will apply the same stable ordering logic to each one.
