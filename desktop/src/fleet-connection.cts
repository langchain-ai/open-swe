const API = "https://api.smith.langchain.com";
const ID = "[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}";
const UUID = new RegExp(`^${ID}$`);
const PATHS = {
  PATCH: new RegExp(`^threads/${ID}$`),
  GET: new RegExp(`^threads/${ID}(?:/state|/runs(?:/${ID}(?:/stream)?)?)?$`),
  POST: new RegExp(
    `^threads(?:/search|/${ID}/runs(?:/stream|/${ID}/cancel)?)?$`,
  ),
};

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export class FleetConnection {
  private current: {
    apiKey: string;
    id: string;
    name: string;
    organizationId: string;
  } | null = null;
  private lifetime = new AbortController();

  constructor(private readonly transport: typeof fetch = fetch) {}

  status() {
    return this.current
      ? { id: this.current.id, name: this.current.name }
      : null;
  }

  disconnect() {
    this.current = null;
    this.lifetime.abort();
    this.lifetime = new AbortController();
  }

  async connect(input: unknown) {
    if (
      !record(input) ||
      typeof input.apiKey !== "string" ||
      !/^\S{10,4096}$/.test(input.apiKey) ||
      typeof input.workspaceId !== "string" ||
      !UUID.test(input.workspaceId)
    )
      throw new Error("Enter a workspace UUID and a valid LangSmith API key.");
    if (this.current) throw new Error("Disconnect before changing workspace.");
    const id = input.workspaceId.toLowerCase();
    const lifetime = this.lifetime;
    const headers = new Headers({
      "x-api-key": input.apiKey,
      "x-tenant-id": id,
    });
    const read = async (path: string): Promise<unknown> => {
      const response = await this.transport(`${API}${path}`, {
        headers,
        redirect: "error",
        credentials: "omit",
        signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(15000)]),
      }).catch(() => {
        throw new Error("Could not reach LangSmith.");
      });
      if (!response.ok)
        throw new Error(
          `LangSmith rejected the connection (HTTP ${response.status}). Check the key’s workspace and metadata permissions.`,
        );
      return response.json().catch(() => {
        throw new Error("LangSmith returned an invalid workspace response.");
      });
    };
    const auth = await read("/auth/public");
    if (
      !record(auth) ||
      auth.tenant_id !== id ||
      typeof auth.organization_id !== "string" ||
      !UUID.test(auth.organization_id)
    )
      throw new Error("This key cannot access that workspace.");
    headers.set("x-organization-id", auth.organization_id);
    const workspace = await read(`/workspaces/${id}`);
    if (
      !record(workspace) ||
      workspace.id !== id ||
      workspace.organization_id !== auth.organization_id ||
      workspace.is_deleted ||
      typeof workspace.display_name !== "string"
    )
      throw new Error("Cannot access workspace metadata.");
    if (workspace.data_plane_url)
      throw new Error(
        "Fleet Desktop currently supports US cloud workspaces only.",
      );
    if (lifetime.signal.aborted || this.current)
      throw new Error("Connection changed. Try again.");
    this.current = {
      apiKey: input.apiKey,
      id,
      name: workspace.display_name,
      organizationId: auth.organization_id,
    };
    return this.status();
  }

  async proxy(request: Request): Promise<Response> {
    const url = new URL(request.url);
    const match = new RegExp(`^/fleet-api/(${ID})/(.+)$`).exec(url.pathname);
    const allowed = PATHS[request.method as keyof typeof PATHS];
    if (
      url.origin !== "null" ||
      url.protocol !== "open-swe:" ||
      url.hostname !== "app" ||
      url.port ||
      url.username ||
      url.password ||
      !match ||
      !allowed?.test(match[2])
    )
      return Response.json(
        { detail: "Unsupported Fleet operation." },
        { status: 403 },
      );
    const current = this.current;
    const lifetime = this.lifetime;
    if (!current)
      return Response.json(
        { detail: "Connect to LangSmith first." },
        { status: 401 },
      );
    if (match[1].toLowerCase() !== current.id)
      return Response.json(
        { detail: "Disconnect before changing workspace." },
        { status: 403 },
      );
    const headers = new Headers({
      "x-api-key": current.apiKey,
      "x-tenant-id": current.id,
      "x-organization-id": current.organizationId,
    });
    for (const name of ["accept", "content-type", "last-event-id"]) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    try {
      let body: Uint8Array<ArrayBuffer> | undefined;
      if (request.body) {
        const reader = request.body.getReader();
        const chunks: Uint8Array[] = [];
        let size = 0;
        try {
          while (true) {
            const next = await reader.read();
            if (next.done) break;
            size += next.value.byteLength;
            if (size > 1024 * 1024) {
              await reader.cancel();
              return Response.json(
                { detail: "Message is too large." },
                { status: 413 },
              );
            }
            chunks.push(next.value);
          }
        } finally {
          reader.releaseLock();
        }
        body = new Uint8Array(size);
        let offset = 0;
        for (const chunk of chunks) {
          body.set(chunk, offset);
          offset += chunk.byteLength;
        }
      }
      if (lifetime.signal.aborted)
        return Response.json(
          { detail: "Connection changed." },
          { status: 401 },
        );
      const response = await this.transport(
        `${API}/v1/fleet/lg/${match[2]}${url.search}`,
        {
          method: request.method,
          headers,
          body,
          credentials: "omit",
          redirect: "error",
          signal: AbortSignal.any([
            lifetime.signal,
            request.signal,
            AbortSignal.timeout(300000),
          ]),
        },
      );
      if (!response.ok) {
        await response.body?.cancel();
        return Response.json(
          { detail: `Fleet rejected the request (HTTP ${response.status}).` },
          { status: response.status },
        );
      }
      const output = new Headers({ "Cache-Control": "no-store" });
      for (const name of ["content-type", "content-location"]) {
        const value = response.headers.get(name);
        if (value) output.set(name, value);
      }
      return new Response(response.body, {
        status: response.status,
        headers: output,
      });
    } catch {
      return Response.json(
        { detail: "Could not reach Fleet. Reconnect and try again." },
        { status: 502 },
      );
    }
  }
}
