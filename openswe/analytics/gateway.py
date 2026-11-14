"""Read a person's gateway spend counters using their own LangSmith grant."""

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

import httpx2
from fastapi import HTTPException
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from openswe.dashboard.langsmith_login import LangSmithLogin
from openswe.dashboard.langsmith_oauth import langsmith_issuer
from openswe.dashboard.principal import DashboardPrincipal
from openswe.utils.http_pool import pooled_transport

logger = logging.getLogger(__name__)


class SpendLimit(BaseModel):
    window: str
    limit_usd: Decimal = Field(ge=0, allow_inf_nan=False)
    providers: list[str] = Field(default_factory=list)

    async def next_reset(self, now: datetime) -> datetime | None:
        start = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if self.window == "hourly":
            return start + timedelta(hours=1)
        start = start.replace(hour=0)
        if self.window == "daily":
            return start + timedelta(days=1)
        if self.window == "weekly":
            return start + timedelta(days=7 - start.weekday())
        if self.window == "monthly":
            return start.replace(
                year=start.year + (start.month == 12), month=start.month % 12 + 1, day=1
            )
        return None


class SpendConfig(BaseModel):
    version: int | None = None
    window: str | None = None
    limit_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    limits: list[SpendLimit] = Field(default_factory=list)

    async def windows(self) -> list[SpendLimit]:
        if self.version in {2, 3}:
            if not self.limits:
                raise ValueError("Missing spend windows")
            return self.limits
        if self.version not in {None, 1} or self.window is None or self.limit_usd is None:
            raise ValueError("Unsupported spend configuration")
        return [SpendLimit(window=self.window, limit_usd=self.limit_usd)]


class SpendCounter(BaseModel):
    window: str
    providers: list[str] = Field(default_factory=list)
    scope: str = ""
    spend_usd: Decimal = Field(ge=0, allow_inf_nan=False)


class SubjectMatcher(BaseModel):
    key: str
    value: str


class SpendPolicy(BaseModel):
    id: str
    name: str
    enabled: bool
    subject_matchers: list[SubjectMatcher]
    config: SpendConfig
    current_spend_usd: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)
    current_spend: list[SpendCounter] = Field(default_factory=list)

    async def personal(self, subject: str) -> bool:
        return self.enabled and self.subject_matchers == [
            SubjectMatcher(key="user_id", value=subject)
        ]

    async def usage(self) -> list[GatewaySpendWindow]:
        now = datetime.now(UTC)
        return [
            GatewaySpendWindow(
                policy_id=self.id,
                name=self.name,
                window=limit.window,
                providers=limit.providers,
                limit_usd=limit.limit_usd,
                resets_at=await limit.next_reset(now),
                spend_usd=(
                    next(
                        (
                            counter.spend_usd
                            for counter in self.current_spend
                            if counter.window == limit.window
                            and set(counter.providers) == set(limit.providers)
                            and counter.scope
                            == (
                                "provider:" + ",".join(sorted(limit.providers))
                                if limit.providers
                                else ""
                            )
                        ),
                        None,
                    )
                    if self.config.version in {2, 3}
                    else self.current_spend_usd
                ),
            )
            for limit in await self.config.windows()
        ]


class GatewaySpendWindow(SpendLimit):
    policy_id: str
    name: str
    spend_usd: Decimal | None
    resets_at: datetime | None


class GatewaySpend(BaseModel):
    status: Literal["ready", "sign_in_required", "forbidden", "unavailable"]
    limits: list[GatewaySpendWindow] = Field(default_factory=list)

    @classmethod
    async def for_person(cls, principal: DashboardPrincipal) -> GatewaySpend:
        try:
            user = await principal.user()
            if user is None:
                return cls(status="sign_in_required")
            identity = next(
                (item for item in user.identities if item.provider == "langsmith"), None
            )
            if identity is None:
                return cls(status="sign_in_required")
            token = await LangSmithLogin().authorize_user(user)
            async with httpx2.AsyncClient(
                timeout=httpx2.Timeout(15.0, connect=5.0), transport=pooled_transport()
            ) as client:
                response = await client.get(
                    langsmith_issuer() + "/v1/platform/gateway-policies",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "policy_type": "spend_cap",
                        "subject_matcher_key": "user_id",
                        "subject_matcher_value": identity.external_id,
                    },
                )
            if response.status_code == 401:
                return cls(status="sign_in_required")
            if response.status_code == 403:
                return cls(status="forbidden")
            response.raise_for_status()
            policies = TypeAdapter(list[SpendPolicy]).validate_python(response.json())
            return cls(
                status="ready",
                limits=[
                    window
                    for policy in policies
                    if await policy.personal(identity.external_id)
                    for window in await policy.usage()
                ],
            )
        except HTTPException as exc:
            logger.warning(
                "Gateway spend authorization failed", extra={"status_code": exc.status_code}
            )
            return cls(
                status="sign_in_required" if exc.status_code in {401, 403} else "unavailable"
            )
        except (httpx2.HTTPError, ValidationError, ValueError) as exc:
            logger.warning("Gateway spend unavailable", extra={"error_type": type(exc).__name__})
            return cls(status="unavailable")
