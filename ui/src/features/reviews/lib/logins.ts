/** A login as people read it: bots without their "[bot]" suffix. */
export function displayName(author: { login: string } | null): string {
  return author?.login.replace(/\[bot\]$/, "") ?? "ghost"
}

/** GitHub logins compare without case. */
export function sameLogin(
  a: string | null | undefined,
  b: string | null | undefined
): boolean {
  return !!a && !!b && a.toLowerCase() === b.toLowerCase()
}
