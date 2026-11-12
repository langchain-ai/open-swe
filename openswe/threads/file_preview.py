"""Raw bytes of a workspace file, behind a signed URL the Files panel can render.

The URL's token is the only credential: a sandboxed preview frame has an opaque
origin and sends no cookies. Whatever a file holds, it is answered with a
``sandbox`` CSP, so it never runs as the dashboard even when opened in its own tab.
"""

import posixpath
import re
import time
import uuid
from dataclasses import dataclass
from typing import Literal, Self
from urllib.parse import quote

import jwt
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError

from openswe.dashboard.oauth import JWT_ALG, _secret
from openswe.dashboard.principal import DashboardPrincipal
from openswe.threads.files import ThreadWorkspace, WorkspaceFileStat

PREVIEW_MAX_BYTES = 25 * 1024 * 1024
PREVIEW_TTL_SECONDS = 60 * 60
PREVIEW_AUDIENCE = "open-swe-file-preview"

# Never "allow-same-origin", "allow-top-navigation*", "allow-popups*" or "allow-forms":
# any of them lets agent-written HTML act as the viewer or leave its frame.
_HTML_CSP = "; ".join(
    [
        "sandbox allow-scripts allow-downloads",
        "default-src 'none'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:",
        "style-src 'self' 'unsafe-inline' https:",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data: https:",
        "media-src 'self' data: blob: https:",
        "connect-src 'none'",
        "form-action 'none'",
        "base-uri 'none'",
        "frame-src 'none'",
        "worker-src 'none'",
    ]
)
_SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
# An HTML page or Markdown document needs its folder: what it links relatively.
_FOLDER_EXTENSIONS = frozenset({".html", ".htm", ".md", ".markdown"})
_DEFAULT_CSP = "default-src 'none'; sandbox"
_UNSAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

_INLINE_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".md": "text/plain; charset=utf-8",
    ".markdown": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/plain; charset=utf-8",
    ".tsv": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".pdf": "application/pdf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}
# What a sandboxed page can load from its folder. A page can read its own link and
# send it out, so data files are never in a folder's reach; they need a link of their own.
_FOLDER_ASSETS = frozenset(
    {
        ".html",
        ".htm",
        ".css",
        ".js",
        ".mjs",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".avif",
        ".bmp",
        ".ico",
        ".svg",
        ".woff",
        ".woff2",
        ".ttf",
        ".otf",
        ".mp4",
        ".webm",
        ".mov",
        ".mp3",
        ".wav",
        ".ogg",
        ".m4a",
    }
)


# On every answer, errors included. The opaque-origin frame needs CORP
# cross-origin and anonymous CORS to load its own images, module scripts and
# fonts; the token in the path is the credential, so neither grants anything.
_ALWAYS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Access-Control-Allow-Origin": "*",
}
ERROR_HEADERS = {**_ALWAYS, "Content-Security-Policy": _DEFAULT_CSP}


@dataclass(frozen=True)
class PreviewFileType:
    """How a file is answered, decided by its extension and never by its bytes."""

    media_type: str
    inline: bool
    csp: str

    @classmethod
    def of(cls, path: str) -> Self:
        extension = posixpath.splitext(path)[1].lower()
        media_type = _INLINE_TYPES.get(extension)
        if media_type is None:
            return cls("application/octet-stream", False, _DEFAULT_CSP)
        if media_type.startswith("text/html"):
            return cls(media_type, True, _HTML_CSP)
        if media_type == "image/svg+xml":
            return cls(media_type, True, _SVG_CSP)
        return cls(media_type, True, _DEFAULT_CSP)

    @staticmethod
    def renders_folder(path: str) -> bool:
        return posixpath.splitext(path)[1].lower() in _FOLDER_EXTENSIONS

    def headers(self, file_name: str) -> dict[str, str]:
        safe = _UNSAFE_FILE_NAME.sub("_", file_name).strip("._-")[:100] or "file"
        disposition = "inline" if self.inline else "attachment"
        return {
            **_ALWAYS,
            "Content-Type": self.media_type,
            "Content-Security-Policy": self.csp,
            "Content-Disposition": f'{disposition}; filename="{safe}"',
            "Accept-Ranges": "bytes",
        }


@dataclass(frozen=True)
class ByteRange:
    """The bytes of a file one response carries; ``partial`` when a Range header chose them."""

    start: int
    end: int
    size: int
    partial: bool

    @classmethod
    def of(cls, header: str | None, size: int) -> Self:
        """The single range ``header`` asks for; anything else gets the whole file."""
        whole = cls(0, size - 1, size, False)
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", (header or "").strip())
        if match is None or match.groups() == ("", ""):
            return whole
        first, last = match.groups()
        if first:
            start, end = int(first), int(last) if last else size - 1
        else:
            start, end = max(size - int(last), 0), size - 1
        if start >= size or start > end:
            raise HTTPException(
                416,
                "Requested range not satisfiable.",
                headers={**ERROR_HEADERS, "Content-Range": f"bytes */{size}"},
            )
        return cls(start, min(end, size - 1), size, True)

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def headers(self) -> dict[str, str]:
        return (
            {"Content-Range": f"bytes {self.start}-{self.end}/{self.size}"} if self.partial else {}
        )


class PreviewIdentity(BaseModel):
    """The session claims ``DashboardPrincipal`` reads, carried so serving re-checks the account."""

    sub: str = Field(min_length=1)
    email: str | None = None
    user_id: str | None = None
    provider: Literal["github", "langsmith"] = "github"
    langsmith_subject: str | None = None

    @classmethod
    def of(cls, session: dict[str, object]) -> Self:
        return cls.model_validate(
            {key: session[key] for key in cls.model_fields if session.get(key) is not None}
        )


class PreviewLink(BaseModel):
    """Where the dashboard fetches a file's bytes, relative to its API base."""

    path: str


class FilePreviewGrant(BaseModel):
    """Who may read which workspace file, or which HTML page's folder, for the next hour."""

    identity: PreviewIdentity
    thread_id: str = Field(min_length=1)
    sandbox_id: str = Field(min_length=1)
    # The workspace root with every symlink resolved, which serving holds it to.
    root: str = Field(min_length=1)
    kind: Literal["file", "directory"]
    # The file, or the page's folder, with every symlink resolved, relative to ``root``.
    path: str

    @classmethod
    async def mint(cls, thread_id: str, session: dict[str, object], path: str) -> PreviewLink:
        identity = PreviewIdentity.of(session)
        workspace = await ThreadWorkspace.open(thread_id, identity.sub, identity.email)
        file = await workspace.stat(path)
        if file.size > PREVIEW_MAX_BYTES:
            raise HTTPException(413, "File is too large to preview.")
        folder = PreviewFileType.renders_folder(file.path)
        grant = cls(
            identity=identity,
            thread_id=thread_id,
            sandbox_id=workspace.sandbox_id,
            root=file.root,
            kind="directory" if folder else "file",
            path=posixpath.dirname(file.path) if folder else file.path,
        )
        name = quote(posixpath.basename(file.path))
        return PreviewLink(
            path=f"/threads/{quote(thread_id, safe='')}/files/raw/{grant.issue()}/{name}"
        )

    def issue(self) -> str:
        now = int(time.time())
        payload = {
            **self.model_dump(mode="json"),
            "aud": PREVIEW_AUDIENCE,
            "iat": now,
            "exp": now + PREVIEW_TTL_SECONDS,
            # Every link is new, so a refresh within the same second still loads afresh.
            "jti": uuid.uuid4().hex,
        }
        return jwt.encode(payload, _secret(), algorithm=JWT_ALG)

    @classmethod
    def decode(cls, token: str, thread_id: str) -> Self:
        try:
            payload = jwt.decode(
                token,
                _secret(),
                algorithms=[JWT_ALG],
                audience=PREVIEW_AUDIENCE,
                options={"require": ["aud", "iat", "exp"]},
            )
            grant = cls.model_validate(payload)
        except (jwt.PyJWTError, ValidationError) as exc:
            raise HTTPException(401, "This preview link expired. Open the file again.") from exc
        if grant.thread_id != thread_id:
            raise HTTPException(401, "This preview link expired. Open the file again.")
        return grant

    async def open(self, request_path: str, method: str) -> ThreadWorkspace:
        """The workspace, if the account and the thread still allow what the grant was minted for."""
        session = self.identity.model_dump()
        principal = DashboardPrincipal.from_session(session)
        principal.authorize_path(request_path, method)
        await principal.authorize(path=request_path)
        return await ThreadWorkspace.open(
            self.thread_id,
            self.identity.sub,
            self.identity.email,
            sandbox_id=self.sandbox_id,
            root=self.root,
        )

    def target(self, rest: str) -> tuple[str, str | None]:
        """The workspace path ``rest`` names under this grant, and the folder it must resolve in."""
        if self.kind == "file":
            if rest != posixpath.basename(self.path):
                raise HTTPException(404, "Path not found.")
            return self.path, None
        segments = rest.split("/")
        if (
            "\0" in rest
            or "\\" in rest
            or any(segment in {"", ".", ".."} or segment.startswith(".") for segment in segments)
            or posixpath.splitext(rest)[1].lower() not in _FOLDER_ASSETS
        ):
            raise HTTPException(404, "Path not found.")
        return posixpath.join(self.path, rest), self.path

    async def stat(self, workspace: ThreadWorkspace, rest: str) -> WorkspaceFileStat:
        path, within = self.target(rest)
        file = await workspace.stat(path, within=within)
        # A root swapped for a symlink would carry the grant somewhere else.
        if file.root != self.root:
            raise HTTPException(404, "The workspace moved. Open the file again.")
        if self.kind == "file" and file.path != self.path:
            raise HTTPException(404, "Path not found.")
        if file.size > PREVIEW_MAX_BYTES:
            raise HTTPException(413, "File is too large to preview.")
        return file
