export type StructuredSenderKind = "person" | "system"

export type ParsedStructuredInput =
  | { type: "entity"; id: string; kind: string; displayName?: string }
  | {
      type: "message"
      content: string
      sender: string
      senderKind: StructuredSenderKind
    }
  | { type: "legacy"; content: string }

export interface StructuredEntity {
  kind: string
  displayName?: string
}

const ENTITY_PATTERN = /^\s*<chat_entity\b([^>]*)>([\s\S]*?)<\/chat_entity>\s*$/
const MESSAGE_PATTERN =
  /^\s*<chat_message\b([^>]*)>([\s\S]*?)<\/chat_message>\s*$/
const CONTENT_PATTERN = /<content>([\s\S]*?)<\/content>/
const ATTRIBUTE_PATTERN = /([A-Za-z_][\w:.-]*)\s*=\s*("[^"]*"|'[^']*')/g

export function decodeXmlText(value: string): string {
  return value.replace(
    /&(?:#(\d+)|#x([\da-fA-F]+)|amp|lt|gt|quot|apos);/g,
    (entity, decimal: string | undefined, hexadecimal: string | undefined) => {
      if (decimal || hexadecimal) {
        const codePoint = Number.parseInt(
          decimal ?? hexadecimal ?? "",
          decimal ? 10 : 16
        )
        if (
          !Number.isInteger(codePoint) ||
          codePoint < 0 ||
          codePoint > 0x10ffff
        )
          return entity
        try {
          return String.fromCodePoint(codePoint)
        } catch {
          return entity
        }
      }
      const named: Record<string, string> = {
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&apos;": "'",
      }
      return named[entity] ?? entity
    }
  )
}

function parseAttributes(source: string): Record<string, string> | null {
  const attributes: Record<string, string> = {}
  let cursor = 0
  ATTRIBUTE_PATTERN.lastIndex = 0
  for (
    let match = ATTRIBUTE_PATTERN.exec(source);
    match;
    match = ATTRIBUTE_PATTERN.exec(source)
  ) {
    if (source.slice(cursor, match.index).trim()) return null
    const name = match[1]
    const quoted = match[2]
    if (!name || !quoted || name in attributes) return null
    attributes[name] = decodeXmlText(quoted.slice(1, -1))
    cursor = ATTRIBUTE_PATTERN.lastIndex
  }
  return source.slice(cursor).trim() ? null : attributes
}

function childText(body: string, tag: string): string | undefined {
  const match = new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`).exec(body)
  return match ? decodeXmlText(match[1] ?? "") : undefined
}

function senderKind(
  attributes: Record<string, string>,
  entities: ReadonlyMap<string, StructuredEntity>
): StructuredSenderKind {
  const explicit = attributes.kind?.toLowerCase()
  if (explicit === "system" || explicit === "automation") return "system"
  if (explicit === "human" || explicit === "person") return "person"
  return entities.get(attributes.sender ?? "")?.kind === "system"
    ? "system"
    : "person"
}

export function parseStructuredInput(
  content: string,
  entities: ReadonlyMap<string, StructuredEntity> = new Map()
): ParsedStructuredInput {
  const entityMatch = ENTITY_PATTERN.exec(content)
  if (entityMatch) {
    const attributes = parseAttributes(entityMatch[1] ?? "")
    const id = attributes?.id
    const kind = attributes?.kind
    if (id && kind) {
      return {
        type: "entity",
        id,
        kind: kind.toLowerCase(),
        displayName: childText(entityMatch[2] ?? "", "display_name"),
      }
    }
  }

  const messageMatch = MESSAGE_PATTERN.exec(content)
  if (messageMatch) {
    const attributes = parseAttributes(messageMatch[1] ?? "")
    const body = messageMatch[2] ?? ""
    const contentMatch = CONTENT_PATTERN.exec(body)
    const remainder = contentMatch
      ? `${body.slice(0, contentMatch.index)}${body.slice(
          contentMatch.index + contentMatch[0].length
        )}`
      : body
    if (attributes?.sender && contentMatch && !remainder.trim()) {
      return {
        type: "message",
        content: decodeXmlText(contentMatch[1] ?? ""),
        sender: attributes.sender,
        senderKind: senderKind(attributes, entities),
      }
    }
  }

  return { type: "legacy", content }
}

export function collectStructuredEntities(
  contents: Iterable<string>
): Map<string, StructuredEntity> {
  const entities = new Map<string, StructuredEntity>()
  for (const content of contents) {
    const parsed = parseStructuredInput(content)
    if (parsed.type !== "entity") continue
    entities.set(parsed.id, {
      kind: parsed.kind,
      displayName: parsed.displayName,
    })
  }
  return entities
}
