import * as Exit from "effect/Exit";
import * as Schema from "effect/Schema";

import { OpenSWEProjectFile, OPEN_SWE_PROJECT_FILE_SCHEMA_URL } from "@openswe/contracts";

import { fromLenientJson } from "./schemaJson.ts";

/**
 * Codec between the raw `openswe.json` file contents (lenient JSONC string) and the
 * decoded {@link OpenSWEProjectFile}.
 */
export const OpenSWEProjectFileFromJson = fromLenientJson(OpenSWEProjectFile);

const decodeOpenSWEProjectFile = Schema.decodeExit(OpenSWEProjectFileFromJson);

/**
 * Decode raw `openswe.json` contents, treating invalid or malformed files as
 * absent. Clients use this to read optional defaults (scripts, thread env
 * mode) without surfacing decode errors to the user.
 */
export function parseOpenSWEProjectFile(contents: string): OpenSWEProjectFile | null {
  const decoded = decodeOpenSWEProjectFile(contents);
  return Exit.isSuccess(decoded) ? decoded.value : null;
}

/**
 * Build the publishable JSON Schema document for `openswe.json` (draft 2020-12).
 *
 * Served from the marketing site at {@link OPEN_SWE_PROJECT_FILE_SCHEMA_URL} so
 * editors get LSP support via a `$schema` reference.
 */
export function buildOpenSWEProjectFileJsonSchema(): Record<string, unknown> {
  const document = Schema.toJsonSchemaDocument(OpenSWEProjectFile);
  const jsonSchema: Record<string, unknown> = {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    $id: OPEN_SWE_PROJECT_FILE_SCHEMA_URL,
    ...document.schema,
  };
  if (document.definitions && Object.keys(document.definitions).length > 0) {
    jsonSchema.$defs = document.definitions;
  }
  return jsonSchema;
}
