"""Per-person OAuth credentials in PostgreSQL: one row per person and provider.

Rows are keyed by the person (``users.id``), not a GitHub login, because logins are
mutable; callers still pass the login they have and it is resolved here. Secrets
arrive already encrypted (``openswe.encryption``) and are never decrypted in this
module. Callers refreshing a credential hold ``refresh_guard`` around the read and
write so concurrent workers never reuse a rotated refresh token.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import ForeignKey, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.users.records import UnknownUser, user_id_for_login

type OAuthProvider = Literal["langsmith"]


class OAuthCredential(Base):
    __tablename__ = "user_oauth_credential"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(primary_key=True)
    encrypted_access_token: Mapped[str]
    client_id: Mapped[str]
    token_endpoint: Mapped[str]
    encrypted_refresh_token: Mapped[str | None] = mapped_column(default=None)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(default=None)
    # For providers that register a client per person (dynamic client registration).
    encrypted_client_secret: Mapped[str | None] = mapped_column(default=None)
    account_email: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


async def load_credential(provider: OAuthProvider, login: str) -> OAuthCredential | None:
    async with postgres.session() as session:
        return await session.scalar(
            select(OAuthCredential).where(
                OAuthCredential.user_id == user_id_for_login(login),
                OAuthCredential.provider == provider,
            )
        )


async def save_credential(
    provider: OAuthProvider,
    login: str,
    *,
    encrypted_access_token: str,
    encrypted_refresh_token: str | None,
    access_token_expires_at: datetime | None,
    client_id: str,
    token_endpoint: str,
    account_email: str | None,
    encrypted_client_secret: str | None = None,
) -> None:
    """Insert or replace the person's credential for ``provider``."""
    async with postgres.session() as session:
        user_id = await session.scalar(select(user_id_for_login(login)))
        if user_id is None:
            raise UnknownUser(login)
        values = {
            "encrypted_access_token": encrypted_access_token,
            "encrypted_refresh_token": encrypted_refresh_token,
            "access_token_expires_at": access_token_expires_at,
            "client_id": client_id,
            "token_endpoint": token_endpoint,
            "account_email": account_email,
            "encrypted_client_secret": encrypted_client_secret,
        }
        upsert = insert(OAuthCredential).values(user_id=user_id, provider=provider, **values)
        await session.execute(
            upsert.on_conflict_do_update(
                index_elements=[OAuthCredential.user_id, OAuthCredential.provider],
                set_={**values, "updated_at": func.clock_timestamp()},
            )
        )


async def delete_credential(provider: OAuthProvider, login: str) -> None:
    async with postgres.session() as session:
        await session.execute(
            delete(OAuthCredential).where(
                OAuthCredential.user_id == user_id_for_login(login),
                OAuthCredential.provider == provider,
            )
        )
