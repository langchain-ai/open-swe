"""Dashboard WebSockets onto a thread's sandbox, opened with a short-lived ticket.

A browser cannot set headers on a WebSocket, so the connect route hands out a
ticket the socket presents as its second subprotocol, after the socket's own.
The URL points straight at the backend: the UI's proxy does not pass upgrades.
"""

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from fastapi import HTTPException, WebSocket

from openswe.dashboard.oauth import decode_terminal_ticket, issue_terminal_ticket
from openswe.utils.thread_ops import langgraph_url


@dataclass(frozen=True)
class ThreadSocket:
    """One kind of live socket a thread offers, at ``/threads/{id}/<path>``."""

    path: str
    protocol: str
    audience: str

    def url(self, thread_id: str) -> str:
        parsed = urlsplit(langgraph_url())
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise HTTPException(500, f"invalid LangGraph URL for the thread {self.path}")
        path = (
            f"{parsed.path.rstrip('/')}/dashboard/api/threads/"
            f"{quote(thread_id, safe='')}/{self.path}"
        )
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunsplit((scheme, parsed.netloc, path, "", ""))

    def connection(self, thread_id: str, session: dict[str, Any]) -> dict[str, str]:
        """What the dashboard needs to open this socket as ``session``'s person."""
        return {
            "url": self.url(thread_id),
            "protocol": self.protocol,
            "ticket": issue_terminal_ticket(
                login=session["sub"],
                email=session.get("email"),
                thread_id=thread_id,
                audience=self.audience,
            ),
        }

    def session(self, websocket: WebSocket, thread_id: str) -> dict[str, Any]:
        """The person whose ticket opened ``websocket``."""
        offered = [
            value.strip()
            for value in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if value.strip()
        ]
        if len(offered) != 2 or offered[0] != self.protocol:
            raise HTTPException(401, f"invalid {self.path} ticket")
        return decode_terminal_ticket(offered[1], thread_id=thread_id, audience=self.audience)
