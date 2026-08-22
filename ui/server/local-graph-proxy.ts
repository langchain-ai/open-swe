import { getRequestURL, proxyRequest } from "h3"

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

export function localGraphTarget(requestUrl: URL, origin: string): string {
  const pathname =
    requestUrl.pathname.replace(/^\/local-graph(?=\/|$)/, "") || "/"
  return `${origin}${pathname}${requestUrl.search}`
}

export default async function localGraphProxy(
  event: Parameters<typeof proxyRequest>[0]
) {
  const { origin, token } = localGraphConfiguration()
  return proxyRequest(event, localGraphTarget(getRequestURL(event), origin), {
    headers: {
      authorization: `Bearer ${token}`,
      "accept-encoding": "identity",
    },
    fetchOptions: { redirect: "manual" },
  })
}
