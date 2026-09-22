"""Per-user repository usage, independent of editable profile settings."""

import logging
from datetime import datetime
from typing import TypedDict
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.users.models import User

logger = logging.getLogger(__name__)


class UserRepositoryUsage(Base):
    __tablename__ = "user_repository_usage"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    repo: Mapped[str] = mapped_column(primary_key=True)
    use_count: Mapped[int] = mapped_column(BigInteger, default=1, server_default="1")
    last_used_at: Mapped[datetime] = mapped_column(server_default=NOW, init=False)


class RepositoryUsage(TypedDict):
    repo: str
    use_count: int
    last_used_at: str


async def record_repository_usage(login: str, repo: str) -> None:
    if not postgres.configured():
        return
    try:
        user = await User.for_login("github", login.strip())
        if user is None:
            logger.warning("Repository usage user not found", extra={"login": login})
            return
        async with postgres.session() as session:
            await session.execute(
                insert(UserRepositoryUsage)
                .values(user_id=user.id, repo=repo.strip().lower())
                .on_conflict_do_update(
                    index_elements=[UserRepositoryUsage.user_id, UserRepositoryUsage.repo],
                    set_={
                        "use_count": UserRepositoryUsage.use_count + 1,
                        "last_used_at": func.clock_timestamp(),
                    },
                )
            )
    except Exception:
        logger.exception("Failed to record repository usage", extra={"login": login, "repo": repo})


async def get_repository_usage(login: str) -> list[RepositoryUsage]:
    if not postgres.configured():
        return []
    user = await User.for_login("github", login.strip())
    if user is None:
        return []
    async with postgres.session() as session:
        rows = await session.scalars(
            select(UserRepositoryUsage)
            .where(UserRepositoryUsage.user_id == user.id)
            .order_by(UserRepositoryUsage.last_used_at.desc(), UserRepositoryUsage.repo)
        )
        return [
            RepositoryUsage(
                repo=row.repo,
                use_count=row.use_count,
                last_used_at=row.last_used_at.isoformat(),
            )
            for row in rows
        ]
