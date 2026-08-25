import * as Schema from "effect/Schema";
import { describe, expect, it } from "vite-plus/test";

import {
  buildOpenSWEProjectFileJsonSchema,
  parseOpenSWEProjectFile,
  OpenSWEProjectFileFromJson,
} from "./opensweProjectFile.ts";

const decodeJson = Schema.decodeUnknownSync(OpenSWEProjectFileFromJson);

describe("buildOpenSWEProjectFileJsonSchema", () => {
  it("emits a draft 2020-12 schema with the published $id", () => {
    const schema = buildOpenSWEProjectFileJsonSchema();

    expect(schema.$schema).toBe("https://json-schema.org/draft/2020-12/schema");
    expect(schema.$id).toBe("https://openswe.codes/schema/openswe.json");
    expect(schema.type).toBe("object");
    expect(schema.additionalProperties).toBe(false);
  });

  it("documents every supported field", () => {
    const schema = buildOpenSWEProjectFileJsonSchema() as {
      properties: Record<
        string,
        {
          description?: string;
          items?: { properties: Record<string, unknown>; required: ReadonlyArray<string> };
        }
      >;
      required?: ReadonlyArray<string>;
    };

    expect(Object.keys(schema.properties).sort()).toEqual([
      "$schema",
      "defaultThreadEnvMode",
      "iconPath",
      "scripts",
    ]);
    expect(schema.required).toBeUndefined();
    expect(schema.properties.iconPath?.description).toContain("Workspace-relative path");
    expect(schema.properties.defaultThreadEnvMode?.description).toContain("new threads start");

    const script = schema.properties.scripts?.items;
    expect(script?.required).toEqual(["name", "command"]);
    expect(Object.keys(script?.properties ?? {}).sort()).toEqual([
      "autoOpenPreview",
      "command",
      "icon",
      "name",
      "previewUrl",
      "runOnWorktreeCreate",
    ]);
  });

  it("stays JSON-serializable", () => {
    const schema = buildOpenSWEProjectFileJsonSchema();
    expect(JSON.parse(JSON.stringify(schema))).toEqual(schema);
  });
});

describe("OpenSWEProjectFileFromJson", () => {
  it("decodes lenient JSONC with comments and trailing commas", () => {
    const decoded = decodeJson(`{
      // team scripts
      "iconPath": "assets/logo.svg",
      "scripts": [
        { "name": "Dev", "command": "pnpm dev", },
      ],
    }`);

    expect(decoded.iconPath).toBe("assets/logo.svg");
    expect(decoded.scripts?.[0]).toEqual({ name: "Dev", command: "pnpm dev" });
  });

  it("fails on malformed JSON", () => {
    expect(() => decodeJson("{ not json")).toThrow();
  });
});

describe("parseOpenSWEProjectFile", () => {
  it("returns the decoded file for valid contents", () => {
    expect(parseOpenSWEProjectFile('{ "defaultThreadEnvMode": "worktree" }')).toEqual({
      defaultThreadEnvMode: "worktree",
    });
  });

  it("returns null for malformed or invalid contents", () => {
    expect(parseOpenSWEProjectFile("{ not json")).toBeNull();
    expect(parseOpenSWEProjectFile('{ "defaultThreadEnvMode": "spaceship" }')).toBeNull();
  });
});
