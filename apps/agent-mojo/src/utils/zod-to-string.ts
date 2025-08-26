import { z } from "zod";
import { truncateOutput } from "./truncate-outputs.js";

export function getMissingKeysFromObjectSchema(
  schema: z.ZodTypeAny,
  obj: Record<string, any>,
): string[] {
  if (!(schema instanceof z.ZodObject)) {
    throw new Error("Schema must be a ZodObject.");
  }

  return Object.keys(schema._def.shape()).filter((key) => !(key in obj));
}

export function zodSchemaToString(schema: z.ZodTypeAny, indent = 0): string {
  const spaces = " ".repeat(indent);

  if (schema instanceof z.ZodObject) {
    const shape = schema._def.shape();
    const lines: string[] = [`${spaces}{`];

    for (const [key, value] of Object.entries(shape)) {
      const fieldSchema = value as z.ZodTypeAny;
      const description = fieldSchema._def.description
        ? ` // ${fieldSchema._def.description}`
        : "";

      if (fieldSchema instanceof z.ZodObject) {
        lines.push(
          `${spaces}  ${key}: ${zodSchemaToString(fieldSchema, indent + 2)}${description}`,
        );
      } else {
        const type = getZodType(fieldSchema);
        lines.push(`${spaces}  ${key}: ${type}${description}`);
      }
    }

    lines.push(`${spaces}}`);
    return lines.join("\n");
  }

  return getZodType(schema);
}

function getZodType(schema: z.ZodTypeAny): string {
  const def = schema._def;

  if (schema instanceof z.ZodString) return "string";
  if (schema instanceof z.ZodNumber) return "number";
  if (schema instanceof z.ZodBoolean) return "boolean";
  if (schema instanceof z.ZodArray) return `${getZodType(def.type)}[]`;
  if (schema instanceof z.ZodOptional)
    return `${getZodType(def.innerType)} | undefined`;
  if (schema instanceof z.ZodNullable)
    return `${getZodType(def.innerType)} | null`;
  if (schema instanceof z.ZodUnion)
    return def.options.map(getZodType).join(" | ");
  if (schema instanceof z.ZodEnum)
    return def.values.map((v: any) => `"${v}"`).join(" | ");

  return def.typeName || "unknown";
}

export function formatBadArgsError(schema: z.ZodTypeAny, args: any) {
  const missingKeys = getMissingKeysFromObjectSchema(schema, args);
  return `Invalid arguments for tool call. Expected:\n${zodSchemaToString(
    schema,
  )}.\nGot:\n${JSON.stringify(args)}\nMissing keys:\n - ${missingKeys.join(
    "\n - ",
  )}\n`;
}

export function safeSchemaToString(schema: unknown): string {
  if (schema instanceof z.ZodType) {
    try {
      const result = zodSchemaToString(schema);
      return truncateOutput(result);
    } catch {
      const result = JSON.stringify(schema); // fallback to JSON.stringify
      return truncateOutput(result);
    }
  } else {
    const result = JSON.stringify(schema);
    return truncateOutput(result);
  }
}

export function safeBadArgsError(
  schema: unknown,
  args: any,
  toolName: string,
): string {
  if (schema instanceof z.ZodType) {
    try {
      const result = formatBadArgsError(schema, args);
      return truncateOutput(result);
    } catch {
      const schemaString = truncateOutput(JSON.stringify(schema));
      return `Invalid arguments for tool "${toolName}". Expected schema: ${schemaString}`;
    }
  } else {
    const schemaString = truncateOutput(JSON.stringify(schema));
    return `Invalid arguments for tool "${toolName}". Expected schema: ${schemaString}`;
  }
}
