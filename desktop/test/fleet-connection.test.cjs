const test = require("node:test");
const assert = require("node:assert/strict");
const { FleetConnection } = require("../build/fleet-connection.cjs");
const workspaceId = "018d50d4-3d52-7000-8000-000000000001";
const organizationId = "018d50d4-3d52-7000-8000-000000000002";
const apiKey = "test-only-not-a-real-key";

function transport(calls) {
  return async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith("/auth/public"))
      return Response.json({
        tenant_id: workspaceId,
        organization_id: organizationId,
      });
    if (url.endsWith(`/workspaces/${workspaceId}`))
      return Response.json({
        id: workspaceId,
        organization_id: organizationId,
        display_name: "Test workspace",
        data_plane_url: null,
      });
    return new Response("event: values\ndata: {}\n\n", {
      headers: {
        "content-type": "text/event-stream",
        "content-location": `/threads/${workspaceId}/runs/${organizationId}`,
      },
    });
  };
}

test("credentials stay native and SDK streaming headers survive", async () => {
  const calls = [];
  const connection = new FleetConnection(transport(calls));
  assert.deepEqual(await connection.connect({ apiKey, workspaceId }), {
    id: workspaceId,
    name: "Test workspace",
  });
  const response = await connection.proxy(
    new Request(
      `open-swe://app/fleet-api/${workspaceId}/threads/${workspaceId}/runs/stream`,
      {
        method: "POST",
        body: "{}",
        headers: { authorization: "untrusted", "x-tenant-id": organizationId },
      },
    ),
  );
  assert.match(await response.text(), /event: values/);
  assert.equal(
    response.headers.get("content-location"),
    `/threads/${workspaceId}/runs/${organizationId}`,
  );
  const forwarded = calls.at(-1);
  assert.equal(forwarded.init.headers.get("x-api-key"), apiKey);
  assert.equal(forwarded.init.headers.get("x-tenant-id"), workspaceId);
  assert.equal(forwarded.init.headers.get("authorization"), null);
  connection.disconnect();
  assert.equal(connection.status(), null);
  assert.equal(
    (
      await connection.proxy(
        new Request(`open-swe://app/fleet-api/${workspaceId}/threads`),
      )
    ).status,
    403,
  );
});

test("rejects other origins, workspaces and arbitrary paths without forwarding", async () => {
  const calls = [];
  const connection = new FleetConnection(transport(calls));
  await connection.connect({ apiKey, workspaceId });
  for (const url of [
    `https://example.com/fleet-api/${workspaceId}/threads/${workspaceId}`,
    `open-swe://app/fleet-api/${organizationId}/threads/${workspaceId}`,
    `open-swe://app/fleet-api/${workspaceId}/assistants/search`,
  ]) {
    assert.equal((await connection.proxy(new Request(url))).status, 403);
  }
  assert.equal(calls.length, 2);
});

test("metadata updates allow only a thread in the connected workspace", async () => {
  const calls = [];
  const connection = new FleetConnection(transport(calls));
  await connection.connect({ apiKey, workspaceId });
  const body = JSON.stringify({
    metadata: { is_pinned: true, openswe_archived: false },
  });
  const url = `open-swe://app/fleet-api/${workspaceId}/threads/${organizationId}`;
  await connection.proxy(new Request(url, { method: "PATCH", body }));
  assert.equal(calls.at(-1).init.method, "PATCH");
  assert.equal(new TextDecoder().decode(calls.at(-1).init.body), body);
  for (const rejected of [
    `${url}/state`,
    url.replace(`/fleet-api/${workspaceId}/`, `/fleet-api/${organizationId}/`),
  ]) {
    assert.equal(
      (await connection.proxy(new Request(rejected, { method: "PATCH", body })))
        .status,
      403,
    );
  }
  assert.equal(calls.length, 3);
});

test("disconnect aborts in-flight requests and removes credentials", async () => {
  const calls = [];
  const connection = new FleetConnection(transport(calls));
  await connection.connect({ apiKey, workspaceId });
  await connection.proxy(
    new Request(
      `open-swe://app/fleet-api/${workspaceId}/threads/${workspaceId}`,
    ),
  );
  const signal = calls.at(-1).init.signal;
  connection.disconnect();
  assert.equal(signal.aborted, true);
  assert.equal(
    (
      await connection.proxy(
        new Request(
          `open-swe://app/fleet-api/${workspaceId}/threads/${workspaceId}`,
        ),
      )
    ).status,
    401,
  );
});
