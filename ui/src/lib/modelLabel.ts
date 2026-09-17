export function safeModelLabel(model: string): string {
  const sanitized = model.replace(/[^A-Za-z0-9._:/+-]/g, "-")
  const separator = sanitized.indexOf(":")
  const prefix = separator === -1 ? "" : sanitized.slice(0, separator + 1)
  const path = separator === -1 ? sanitized : sanitized.slice(separator + 1)
  const name = path.split("/").at(-1) ?? ""
  return `${prefix}${name}`.slice(0, 48).replace(/^-+|-+$/g, "")
}
