export function pageLink(base: string, path: string): string | undefined {
  try {
    if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\"))
      return
    const origin = new URL(base)
    const url = new URL(`${base.replace(/\/$/, "")}${path}`)
    const local =
      origin.protocol === "http:" &&
      ["127.0.0.1", "localhost"].includes(origin.hostname)
    if (
      (!local && origin.protocol !== "https:") ||
      origin.username ||
      origin.password ||
      origin.search ||
      origin.hash ||
      url.origin !== origin.origin
    )
      return
    return url.href
  } catch {
    return
  }
}
