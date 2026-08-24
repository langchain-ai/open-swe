interface LocalGraphConfiguration {
  origin: string
  token: string
}

export function localGraphConfiguration(
  environment: NodeJS.ProcessEnv = process.env
): LocalGraphConfiguration {
  const origin = new URL(environment.OPEN_SWE_LOCAL_GRAPH_ORIGIN || "")
  if (
    origin.protocol !== "http:" ||
    !["127.0.0.1", "::1"].includes(origin.hostname) ||
    origin.pathname !== "/" ||
    origin.search ||
    origin.hash
  ) {
    throw new Error(
      "OPEN_SWE_LOCAL_GRAPH_ORIGIN must be a loopback HTTP origin"
    )
  }
  const token = environment.OPEN_SWE_LOCAL_GRAPH_TOKEN
  if (!token) throw new Error("OPEN_SWE_LOCAL_GRAPH_TOKEN is not configured")
  return { origin: origin.origin, token }
}

/** A request against the local graph server, already authorized. */
export async function graphRequest(
  pathname: string,
  init: RequestInit = {}
): Promise<Response> {
  const { origin, token } = localGraphConfiguration()
  return fetch(`${origin}${pathname}`, {
    ...init,
    headers: {
      authorization: `Bearer ${token}`,
      "content-type": "application/json",
      ...init.headers,
    },
  })
}
