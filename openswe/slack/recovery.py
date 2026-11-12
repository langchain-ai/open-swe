"""Session-bound proof for recovering an existing Slack-linked account."""

import time
from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import UUID

import jwt
from fastapi import HTTPException

from openswe.config import ENV
from openswe.dashboard.oauth import JWT_ALG, STATE_TTL_SECONDS, frontend_base_url
from openswe.encryption import decrypt_token, encrypt_token


@dataclass(frozen=True)
class SlackRecovery:
    user_id: UUID
    slack_user_id: str

    @staticmethod
    async def signing_key() -> str:
        key = ENV.DASHBOARD_JWT_SECRET.get()
        if not key:
            raise HTTPException(500, "Dashboard signing key is not configured")
        return key

    async def issue(self) -> str:
        """Keep verified Slack proof private and bound to its initiating person."""
        now = int(time.time())
        return encrypt_token(
            jwt.encode(
                {
                    "aud": "robin-slack-recovery",
                    "sub": str(self.user_id),
                    "slack_user_id": self.slack_user_id,
                    "iat": now,
                    "exp": now + STATE_TTL_SECONDS,
                },
                await SlackRecovery.signing_key(),
                algorithm=JWT_ALG,
            )
        )

    @classmethod
    async def read(cls, token: str, user_id: UUID) -> SlackRecovery:
        """Reject expired, altered, cross-purpose and cross-person proofs."""
        try:
            encoded = decrypt_token(token)
            claims = jwt.decode(
                encoded or "",
                await SlackRecovery.signing_key(),
                algorithms=[JWT_ALG],
                audience="robin-slack-recovery",
                options={"require": ["exp", "iat", "sub", "slack_user_id"]},
            )
            if claims["sub"] != str(user_id) or not isinstance(claims["slack_user_id"], str):
                raise ValueError("recovery person mismatch")
            return cls(user_id, claims["slack_user_id"])
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(
                400, "Slack recovery expired or changed — connect Slack again"
            ) from exc

    @staticmethod
    async def redirect(token: str, error: str = "") -> str:
        query = {"slack_recovery": token}
        if error:
            query["recovery_error"] = error
        return f"{frontend_base_url()}/my-settings/connections?{urlencode(query)}"
