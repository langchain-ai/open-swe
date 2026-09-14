"""A local Slack HTTP boundary for tests that exercise the real SDK."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qsl, urlparse


class SlackAPI:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[int, Any, dict[str, str]]] = []
        self.handler: Any = None
        self.base_url = ""

    def respond(
        self, data: Any, *, status: int = 200, headers: dict[str, str] | None = None
    ) -> None:
        self.responses.append((status, data, headers or {}))


@contextmanager
def slack_api_server() -> Iterator[SlackAPI]:
    api = SlackAPI()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.reply()

        def do_POST(self) -> None:
            self.reply()

        def reply(self) -> None:
            parsed = urlparse(self.path)
            params: dict[str, Any] = dict(parse_qsl(parsed.query))
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if body:
                params.update(
                    json.loads(body)
                    if "application/json" in self.headers.get("Content-Type", "")
                    else dict(parse_qsl(body.decode()))
                )
            method = parsed.path.rsplit("/", 1)[-1]
            api.calls.append((method, params))
            if api.handler:
                status, data, headers = api.handler(method, params, self.headers)
            elif api.responses:
                status, data, headers = api.responses.pop(0)
            else:
                status, data, headers = 200, {"ok": True, "ts": "1.0"}, {}
            content = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            self.send_response(status)
            self.send_header(
                "Content-Type",
                next(
                    (value for name, value in headers.items() if name.lower() == "content-type"),
                    "application/json",
                ),
            )
            self.send_header("Content-Length", str(len(content)))
            for name, value in headers.items():
                if name.lower() not in {"content-length", "content-type"}:
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    api.base_url = f"http://127.0.0.1:{server.server_port}/api/"
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield api
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
