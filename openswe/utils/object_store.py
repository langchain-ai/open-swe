"""An S3-compatible bucket, reached over its REST API with requests signed by AWS SigV4.

GCS (through its XML API with an HMAC key), S3, R2 and MinIO all speak it, so one
client serves every deployment without a cloud SDK.
"""

import hashlib
import hmac
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import httpx2

from openswe.config import ENV
from openswe.utils.http_pool import pooled_transport


class ObjectStoreError(RuntimeError):
    """Object storage could not be reached, or refused a request."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class ListingPage:
    """One page of a ListObjectsV2 answer."""

    objects: list[StoredObject]
    next_token: str | None
    """Where the next page starts; ``None`` on the last page."""

    @classmethod
    def parse(cls, xml: bytes) -> Self:
        """Reads the page whatever namespace it uses: S3's, GCS's, or none."""
        root = ElementTree.fromstring(xml)
        namespace = root.tag[: root.tag.index("}") + 1] if root.tag.startswith("{") else ""
        objects = [
            StoredObject(key, datetime.fromisoformat(modified))
            for item in root.iter(f"{namespace}Contents")
            if (key := item.findtext(f"{namespace}Key"))
            and (modified := item.findtext(f"{namespace}LastModified"))
        ]
        truncated = (root.findtext(f"{namespace}IsTruncated") or "").lower() == "true"
        token = root.findtext(f"{namespace}NextContinuationToken")
        return cls(objects, token if truncated and token else None)


@dataclass(frozen=True, slots=True)
class ObjectStore:
    """One bucket, addressed path-style: ``<url>/<bucket>/<key>``."""

    url: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str

    @classmethod
    def configured(cls) -> Self | None:
        """The bucket the environment names, or ``None`` when object storage is not set up."""
        url, bucket = ENV.OBJECT_STORE_URL.optional(), ENV.OBJECT_STORE_BUCKET.optional()
        key_id = ENV.OBJECT_STORE_ACCESS_KEY_ID.optional()
        secret = ENV.OBJECT_STORE_SECRET_ACCESS_KEY.optional()
        if not (url and bucket and key_id and secret):
            return None
        return cls(url.rstrip("/"), bucket, ENV.OBJECT_STORE_REGION.get(), key_id, secret)

    @staticmethod
    def query_string(query: Mapping[str, str]) -> str:
        """``query`` as SigV4 signs it, sorted and percent-encoded, and as the request sends it."""
        return "&".join(
            f"{quote(name, safe='-_.~')}={quote(value, safe='-_.~')}"
            for name, value in sorted(query.items())
        )

    def authorization(
        self,
        method: str,
        host: str,
        path: str,
        query: Mapping[str, str],
        headers: Mapping[str, str],
        now: datetime,
    ) -> str:
        """The SigV4 ``Authorization`` header, signing ``headers`` (which carry the payload hash)."""
        date = now.strftime("%Y%m%d")
        scope = f"{date}/{self.region}/s3/aws4_request"
        signed = {"host": host, **{name.lower(): value.strip() for name, value in headers.items()}}
        names = sorted(signed)
        canonical = "\n".join(
            [
                method,
                quote(path, safe="/-_.~"),
                self.query_string(query),
                "".join(f"{name}:{signed[name]}\n" for name in names),
                ";".join(names),
                signed["x-amz-content-sha256"],
            ]
        )
        to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                now.strftime("%Y%m%dT%H%M%SZ"),
                scope,
                hashlib.sha256(canonical.encode()).hexdigest(),
            ]
        )
        key = f"AWS4{self.secret_access_key}".encode()
        for part in (date, self.region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        return (
            f"AWS4-HMAC-SHA256 Credential={self.access_key_id}/{scope}, "
            f"SignedHeaders={';'.join(names)}, Signature={signature}"
        )

    async def _request(
        self,
        method: str,
        key: str = "",
        *,
        query: Mapping[str, str] | None = None,
        content: bytes = b"",
        content_type: str | None = None,
    ) -> httpx2.Response:
        endpoint = urlsplit(self.url)
        path = f"{endpoint.path}/{self.bucket}" + (f"/{key}" if key else "")
        host = endpoint.netloc
        now = datetime.now(UTC)
        headers = {
            "x-amz-content-sha256": hashlib.sha256(content).hexdigest(),
            "x-amz-date": now.strftime("%Y%m%dT%H%M%SZ"),
        }
        if content_type is not None:
            headers["content-type"] = content_type
        headers["authorization"] = self.authorization(method, host, path, query or {}, headers, now)
        url = f"{endpoint.scheme}://{host}{quote(path, safe='/-_.~')}"
        if query:
            url += f"?{self.query_string(query)}"
        try:
            async with httpx2.AsyncClient(transport=pooled_transport(), timeout=60) as client:
                response = await client.request(method, url, headers=headers, content=content)
        except httpx2.HTTPError as exc:
            raise ObjectStoreError(f"object storage could not be reached: {exc}") from exc
        if response.is_error and response.status_code != 404:
            raise ObjectStoreError(f"object storage answered {method} with {response.status_code}")
        return response

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        response = await self._request("PUT", key, content=content, content_type=content_type)
        if response.status_code == 404:
            raise ObjectStoreError(f"object storage has no bucket {self.bucket}")

    async def get(self, key: str) -> bytes | None:
        """The object's bytes, or ``None`` when there is none under ``key``."""
        response = await self._request("GET", key)
        return None if response.status_code == 404 else response.content

    async def delete(self, key: str) -> None:
        """Deletes the object; deleting one already gone succeeds."""
        await self._request("DELETE", key)

    async def list(self, prefix: str) -> AsyncIterator[StoredObject]:
        """Every object whose key starts with ``prefix``, in key order."""
        token: str | None = None
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if token is not None:
                query["continuation-token"] = token
            response = await self._request("GET", query=query)
            if response.status_code == 404:
                raise ObjectStoreError(f"object storage has no bucket {self.bucket}")
            page = ListingPage.parse(response.content)
            for stored in page.objects:
                yield stored
            if page.next_token is None:
                return
            token = page.next_token
