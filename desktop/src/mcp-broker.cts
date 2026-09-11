const http = require("node:http");
const { randomBytes, timingSafeEqual } = require("node:crypto");

async function startMcpBroker(backendFetch, getBackendUrl, configPath) {
  const token = randomBytes(32).toString("hex");
  const server = http.createServer(async (request, response) => {
    const supplied = Buffer.from(request.headers.authorization || "");
    const expected = Buffer.from(`Bearer ${token}`);
    if (
      supplied.length !== expected.length ||
      !timingSafeEqual(supplied, expected)
    ) {
      response.writeHead(401).end();
      return;
    }
    const routes = {
      "GET /catalog": "/desktop/mcps",
      "POST /call": "/desktop/mcps/call",
    };
    const route = routes[`${request.method} ${request.url}`];
    if (!route) {
      response.writeHead(404).end();
      return;
    }
    try {
      let body = "";
      for await (const chunk of request) {
        body += chunk;
        if (Buffer.byteLength(body) > 1024 * 1024)
          throw new Error("Request too large");
      }
      const backend = getBackendUrl();
      if (request.method === "POST" && JSON.parse(body).backend !== backend) {
        response.writeHead(409).end();
        return;
      }
      const upstream = await backendFetch(
        new URL(`/dashboard/api${route}`, backend).toString(),
        {
          method: request.method,
          headers: { "content-type": "application/json" },
          ...(request.method === "POST" ? { body } : {}),
          signal: AbortSignal.timeout(60000),
        },
      );
      response.writeHead(upstream.status, {
        "content-type": "application/json",
        "cache-control": "no-store",
      });
      response.end(
        request.method === "GET" && upstream.ok
          ? JSON.stringify({ ...(await upstream.json()), backend })
          : await upstream.text(),
      );
    } catch {
      response.writeHead(502).end();
    }
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  return {
    env: {
      OPEN_SWE_DESKTOP_MCP_URL: `http://127.0.0.1:${server.address().port}`,
      OPEN_SWE_DESKTOP_MCP_TOKEN: token,
      OPEN_SWE_LOCAL_MCPS_FILE: configPath,
    },
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

module.exports = { startMcpBroker };
