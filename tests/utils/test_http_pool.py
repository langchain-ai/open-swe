import asyncio

import httpx2
from aiohttp import web

from openswe.utils.http_pool import PooledAsyncClient, close_pool


class Server:
    def __init__(self) -> None:
        self.connections: list[asyncio.Transport | None] = []

    async def respond(self, request: web.Request) -> web.Response:
        self.connections.append(request.transport)
        return web.json_response(
            {
                "authorization": request.headers.get("Authorization"),
                "cookie": request.headers.get("Cookie"),
            },
            headers={"Set-Cookie": "session=first"},
        )


async def test_clients_reuse_connections_without_sharing_credentials_or_cookies() -> None:
    server = Server()
    app = web.Application()
    app.router.add_get("/", server.respond)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    host, port = runner.addresses[0]
    url = f"http://{host}:{port}/"
    try:
        async with PooledAsyncClient(headers={"Authorization": "Bearer first"}) as client:
            assert (await client.get(url)).json() == {
                "authorization": "Bearer first",
                "cookie": None,
            }
        async with PooledAsyncClient() as client:
            assert (await client.get(url)).json() == {"authorization": None, "cookie": None}
        assert server.connections[0] is server.connections[1]
        await close_pool()
        async with PooledAsyncClient() as client:
            await client.get(url)
        assert server.connections[2] is not server.connections[0]
    finally:
        await close_pool()
        await runner.cleanup()


async def test_explicit_transport_overrides_the_pool() -> None:
    transport = httpx2.MockTransport(lambda request: httpx2.Response(201))
    async with PooledAsyncClient(transport=transport) as client:
        assert (await client.get("https://example.com")).status_code == 201
