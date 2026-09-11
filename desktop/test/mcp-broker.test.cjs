const { test } = require("node:test");
const assert = require("node:assert/strict");
const { startMcpBroker } = require("../build/mcp-broker.cjs");

test("MCP broker authenticates and binds calls to the catalog backend", async () => {
  let backend = "https://cloud.example";
  let calls = 0;
  const broker = await startMcpBroker(
    async () => {
      calls++;
      return Response.json({ login: "alice", tools: [] });
    },
    () => backend,
    "/tmp/mcp.json",
  );
  const url = broker.env.OPEN_SWE_DESKTOP_MCP_URL;
  const headers = {
    authorization: `Bearer ${broker.env.OPEN_SWE_DESKTOP_MCP_TOKEN}`,
  };
  try {
    assert.equal((await fetch(`${url}/catalog`)).status, 401);
    const catalog = await (await fetch(`${url}/catalog`, { headers })).json();
    assert.equal(catalog.backend, backend);
    backend = "https://other.example";
    assert.equal(
      (
        await fetch(`${url}/call`, {
          method: "POST",
          headers,
          body: JSON.stringify({ backend: catalog.backend }),
        })
      ).status,
      409,
    );
    assert.equal(calls, 1);
  } finally {
    await broker.close();
  }
});
