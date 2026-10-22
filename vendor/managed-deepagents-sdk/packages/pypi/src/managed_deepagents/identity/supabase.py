"""Supabase validated-token provider helpers.

Authoring-time sugar for validated-token provider configs that talk to Supabase
Auth (project refs, custom domains, multi-region introspection).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from managed_deepagents.identity import (
        IntrospectionRegionValue,
        ValidatedTokenProvider,
    )


class SupabaseRegionObject(TypedDict, total=False):
    project_ref: str
    url: str
    anon_key: str
    headers: dict[str, str]


SupabaseRegion = str | SupabaseRegionObject


def _supabase_base(project_ref: str) -> str:
    return f"https://{project_ref}.supabase.co/auth/v1"


def supabase_auth_v1_base(url: str) -> str:
    """Normalize a Supabase project or custom-domain URL to the ``…/auth/v1`` root."""
    trimmed = url.rstrip("/")
    trimmed = trimmed.removesuffix("/user")
    if trimmed.endswith("/auth/v1"):
        return trimmed
    return f"{trimmed}/auth/v1"


def _supabase_region_issuers(regions: dict[str, SupabaseRegion]) -> list[str]:
    """Collect JWT issuers for multi-region introspect routing (no network)."""
    issuers: list[str] = []
    seen: set[str] = set()
    for entry in regions.values():
        if isinstance(entry, str):
            issuer = supabase_auth_v1_base(entry)
        elif entry.get("project_ref"):
            issuer = _supabase_base(entry["project_ref"])
        elif entry.get("url"):
            issuer = supabase_auth_v1_base(entry["url"])
        else:
            continue
        if issuer not in seen:
            seen.add(issuer)
            issuers.append(issuer)
    return issuers


def _normalize_supabase_regions(
    regions: dict[str, SupabaseRegion],
) -> dict[str, IntrospectionRegionValue]:
    """Normalize authoring-time Supabase regions into the runtime ``regions`` shape.

    A bare string stays a URL (using the shared base ``apikey``); a dict resolves its
    ``url`` from ``url``/``project_ref`` and, when given, carries region-specific
    ``headers``/``anon_key`` that override the shared ``apikey`` at introspection time.
    """
    out: dict[str, IntrospectionRegionValue] = {}
    for region, entry in regions.items():
        if isinstance(entry, str):
            out[region] = entry
            continue
        url = entry.get("url")
        if not url and entry.get("project_ref"):
            url = f"{_supabase_base(entry['project_ref'])}/user"
        if not url:
            msg = f'auth.supabase(...) region "{region}" needs a `project_ref` or `url`.'
            raise ValueError(msg)
        headers = entry.get("headers")
        if headers is None and entry.get("anon_key"):
            headers = {"apikey": entry["anon_key"]}
        if headers:
            out[region] = {"url": str(url), "headers": cast("dict[str, str]", headers)}
        else:
            out[region] = {"url": str(url)}
    return out


def supabase(
    *,
    project_ref: str | None = None,
    url: str | None = None,
    introspect: bool = False,
    regions: dict[str, SupabaseRegion] | None = None,
    region_header: str = "x-supabase-region",
) -> ValidatedTokenProvider:
    """Build a Supabase validated-token provider.

    Single region JWKS: pass ``project_ref`` (``*.supabase.co``) or ``url``
    (project URL or custom auth domain). Custom domains set ``discovery_url``
    so the runtime resolves the real JWT ``iss`` + JWKS via OIDC (vanity
    hosts are not the token issuer).

    Legacy introspection: pass ``introspect=True``. Single-region introspect
    also sets ``issuer`` for multi-provider routing. Multi-region
    ``regions`` sets ``issuers`` from each region's project ref / URL.
    """
    if project_ref and url:
        msg = "auth.supabase(...) accepts only one of `project_ref` or `url`."
        raise ValueError(msg)

    if introspect:
        if regions:
            provider: ValidatedTokenProvider = {
                "id": "supabase",
                "introspect": {
                    "region_header": region_header,
                    "regions": _normalize_supabase_regions(regions),
                    "headers": {"apikey": "${SUPABASE_ANON_KEY}"},
                },
                "claims": {"user": "email"},
            }
            issuers = _supabase_region_issuers(regions)
            if issuers:
                provider["issuers"] = issuers
            return provider
        if url:
            auth_base = supabase_auth_v1_base(url)
            return {
                "id": "supabase",
                "discovery_url": auth_base,
                "introspect": {
                    "url": f"{auth_base}/user",
                    "headers": {"apikey": "${SUPABASE_ANON_KEY}"},
                },
                "claims": {"user": "email"},
            }
        if not project_ref:
            msg = (
                "auth.supabase(...) requires `project_ref`, `url`, or `regions` "
                "when introspect=True."
            )
            raise ValueError(msg)
        base = _supabase_base(project_ref)
        return {
            "id": "supabase",
            "issuer": base,
            "introspect": {
                "url": f"{base}/user",
                "headers": {"apikey": "${SUPABASE_ANON_KEY}"},
            },
            "claims": {"user": "email"},
        }

    if url:
        return {
            "id": "supabase",
            "discovery_url": supabase_auth_v1_base(url),
            "audience": "authenticated",
            "algorithms": ["ES256", "RS256"],
            "claims": {"user": "email"},
        }
    if not project_ref:
        msg = "auth.supabase(...) without `introspect` requires `project_ref` or `url`."
        raise ValueError(msg)
    base = _supabase_base(project_ref)
    return {
        "id": "supabase",
        "issuer": base,
        "jwks": f"{base}/.well-known/jwks.json",
        "audience": "authenticated",
        "algorithms": ["ES256", "RS256"],
        "claims": {"user": "email"},
    }
