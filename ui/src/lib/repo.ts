export class RepositoryLabels {
  private readonly owner: string | null

  constructor(fullNames: ReadonlyArray<string>) {
    const owners = new Set(
      fullNames.map((name) => name.split("/")[0]?.toLowerCase())
    )
    this.owner =
      owners.size === 1 ? (owners.values().next().value ?? null) : null
  }

  label(fullName: string): string {
    const [owner, name] = fullName.split("/")
    return name && owner?.toLowerCase() === this.owner ? name : fullName
  }
}

/** Normalize GitHub repo input to `owner/name`. */
export function normalizeRepoFullName(raw: string): string | null {
  let v = raw.trim()
  for (const prefix of [
    "https://github.com/",
    "http://github.com/",
    "github.com/",
  ]) {
    if (v.toLowerCase().startsWith(prefix)) {
      v = v.slice(prefix.length)
    }
  }
  v = v.replace(/\/$/, "").replace(/\.git$/, "")
  const parts = v.split("/").filter(Boolean)
  if (parts.length !== 2) return null
  return `${parts[0]}/${parts[1]}`
}
