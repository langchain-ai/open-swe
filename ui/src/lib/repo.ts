/** Normalize GitHub repo input to `owner/name`. */
export function normalizeRepoFullName(raw: string): string | null {
  let v = raw.trim();
  for (const prefix of ["https://github.com/", "http://github.com/", "github.com/"]) {
    if (v.toLowerCase().startsWith(prefix)) {
      v = v.slice(prefix.length);
    }
  }
  v = v.replace(/\/$/, "").replace(/\.git$/, "");
  const parts = v.split("/").filter(Boolean);
  if (parts.length !== 2) return null;
  return `${parts[0]}/${parts[1]}`;
}
