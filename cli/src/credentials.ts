/** The cookie the dashboard's login mints, which the desktop app also stores. */
const SESSION_COOKIE = "osw_session"

/**
 * How the CLI proves who it is. A person signs in or uses their LangSmith
 * personal access token; a LangSmith service key is the workspace's bot, a
 * machine the server lets start only system threads.
 */
export interface Credential {
  readonly machine: boolean
  /** What kind of credential this is and where it came from, for `robin auth status`. */
  readonly source: string
  /** What to tell the user when the server answers 401. */
  readonly rejected: string
  headers(backend: string): Promise<Record<string, string>>
}

export interface StoredSession {
  session: string
  path: string
}

/**
 * A person's dashboard session, sent as the dashboard's own cookie the way the
 * desktop app sends it. `Origin` is the backend's own, which its CSRF check
 * allows: the cookie here is held deliberately, not ambient in a browser.
 */
export class SessionCredential implements Credential {
  readonly machine = false
  readonly rejected = "your session expired — run `robin login` again"

  constructor(
    private readonly session: string,
    readonly source: string
  ) {}

  async headers(backend: string): Promise<Record<string, string>> {
    return { Cookie: `${SESSION_COOKIE}=${this.session}`, Origin: backend }
  }
}

/**
 * A LangSmith API key from Robin's LangSmith workspace: a personal access token
 * (`lsv2_pt_…`) is its person, a service key (`lsv2_sk_…`) is the bot.
 */
export class ApiKeyCredential implements Credential {
  readonly machine: boolean
  readonly source = "LangSmith API key (OPEN_SWE_API_KEY)"
  readonly rejected =
    "the LangSmith API key was rejected — it may have expired, been revoked, or belong to another workspace"

  constructor(private readonly key: string) {
    this.machine = !key.startsWith("lsv2_pt_")
  }

  async headers(): Promise<Record<string, string>> {
    return { Authorization: `Bearer ${this.key}` }
  }
}

/** The first credential the environment offers: an API key, then a person's session. */
export function resolveCredential(
  stored: StoredSession | null
): Credential | null {
  const env = process.env
  const key = env["OPEN_SWE_API_KEY"]
  if (key) return new ApiKeyCredential(key)
  const session = env["OPEN_SWE_SESSION"]
  if (session)
    return new SessionCredential(session, "session (OPEN_SWE_SESSION)")
  return stored === null
    ? null
    : new SessionCredential(stored.session, `session (${stored.path})`)
}
