export function safeModelLabel(model: string): string {
  const sanitized = model.replace(/[^A-Za-z0-9._:/+-]/g, "-")
  return (
    sanitized
      .split("/")
      .at(-1)
      ?.slice(0, 48)
      .replace(/^-+|-+$/g, "") ?? ""
  )
}
