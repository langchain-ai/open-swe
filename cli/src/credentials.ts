import { isRecord, numberAt, parseJson, stringAt } from "./json.ts"

/** The cookie the dashboard's login mints, which the desktop app also stores. */
const SESSION_COOKIE = "osw_session"
/** Fetch a fresh workflow token this long before the current one expires. */
const REFRESH_MARGIN_MS = 60_000
/** Assumed lifetime of a workflow token whose `exp` cannot be read. */
const FALLBACK_LIFETIME_MS = 4 * 60_000

/**
 * How the CLI proves who it is. A person signs in; an API key and a CI
 * workflow are machines, which the server lets start only system threads.
 */
export interface Credential {
  readonly machine: boolean
  /** What to tell the user when the server answers 401. */
  readonly rejected: string
  headers(backend: string): Promise<Record<string, string>>
}

/**
 * A person's dashboard session, sent as the dashboard's own cookie the way the
 * desktop app sends it. `Origin` is the backend's own, which its CSRF check
 * allows: the cookie here is held deliberately, not ambient in a browser.
 */
export class SessionCredential implements Credential {
  readonly machine = false
  readonly rejected = "your session expired — run `open-swe login` again"

  constructor(private readonly session: string) {}

  async headers(backend: string): Promise<Record<string, string>> {
    return { Cookie: `${SESSION_COOKIE}=${this.session}`, Origin: backend }
  }
}

/** An admin-minted workspace API key (`osk_…`). */
export class ApiKeyCredential implements Credential {
  readonly machine = true
  readonly rejected =
    "the API key was rejected — it may have expired or been revoked"

  constructor(private readonly key: string) {}

  async headers(): Promise<Record<string, string>> {
    return { Authorization: `Bearer ${this.key}` }
  }
}

/** Seconds-since-epoch `exp` of a JWT, without verifying it. */
export function jwtExpiry(token: string): number | null {
  const payload = token.split(".")[1]
  if (payload === undefined) return null
  const claims = parseJson(Buffer.from(payload, "base64url").toString("utf8"))
  return isRecord(claims) ? numberAt(claims, "exp") : null
}

/**
 * A GitHub Actions job's OIDC token, which the server trusts for repositories
 * an admin let start threads. Needs `permissions: id-token: write`. Tokens are
 * short-lived, so one is fetched again shortly before it expires.
 */
export class GitHubActionsCredential implements Credential {
  readonly machine = true
  readonly rejected =
    "the GitHub Actions token was rejected — check that an admin let this repository start threads"
  private token: string | null = null
  private refreshAt = 0

  constructor(
    private readonly requestUrl: string,
    private readonly requestToken: string,
    private readonly audience: string
  ) {}

  async headers(): Promise<Record<string, string>> {
    if (this.token === null || Date.now() >= this.refreshAt) {
      this.token = await this.fetchToken()
      const exp = jwtExpiry(this.token)
      this.refreshAt =
        exp === null
          ? Date.now() + FALLBACK_LIFETIME_MS
          : exp * 1_000 - REFRESH_MARGIN_MS
    }
    return { Authorization: `Bearer ${this.token}` }
  }

  private async fetchToken(): Promise<string> {
    const url = new URL(this.requestUrl)
    url.searchParams.set("audience", this.audience)
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${this.requestToken}` },
    })
    if (!response.ok) {
      throw new Error(
        `GitHub Actions refused an OIDC token (HTTP ${response.status})`
      )
    }
    const parsed = parseJson(await response.text())
    const value = isRecord(parsed) ? stringAt(parsed, "value") : null
    if (value === null) throw new Error("GitHub Actions returned no OIDC token")
    return value
  }
}

/**
 * The first credential the environment offers: an API key, then the running
 * GitHub Actions job's identity, then a person's session.
 */
export function resolveCredential(
  backend: string,
  storedSession: string | undefined
): Credential | null {
  const env = process.env
  const key = env["OPEN_SWE_API_KEY"]
  if (key) return new ApiKeyCredential(key)
  const requestUrl = env["ACTIONS_ID_TOKEN_REQUEST_URL"]
  const requestToken = env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]
  if (requestUrl && requestToken) {
    return new GitHubActionsCredential(
      requestUrl,
      requestToken,
      env["OPEN_SWE_OIDC_AUDIENCE"] || backend
    )
  }
  const session = env["OPEN_SWE_SESSION"] || storedSession
  return session ? new SessionCredential(session) : null
}
