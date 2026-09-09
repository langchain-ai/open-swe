"""Per-user dashboard data: one store namespace per login, one typed document per feature.

Every document lives at ``["users", <login>]`` under its feature key, so all of a
user's data is one prefix search away while each feature still writes its own
record and cannot clobber another's.
"""

from pydantic import BaseModel, Field

from agent.store import delete_value, get_value, now_iso, put_value

USERS_NAMESPACE = "users"


class UserDocument[RecordT: BaseModel]:
    def __init__(self, key: str, model: type[RecordT]) -> None:
        self.key = key
        self.model = model

    @staticmethod
    def namespace(login: str) -> list[str]:
        return [USERS_NAMESPACE, login]

    async def get(self, login: str) -> RecordT | None:
        value = await get_value(self.namespace(login), self.key)
        return None if value is None else self.model.model_validate(value)

    async def put(self, login: str, record: RecordT) -> RecordT:
        await put_value(self.namespace(login), self.key, record.model_dump(mode="json"))
        return record

    async def delete(self, login: str) -> None:
        await delete_value(self.namespace(login), self.key)


class ReviewQueueRepos(BaseModel):
    """Repos whose ready-to-review pull requests the user wants listed."""

    repos: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)


REVIEW_QUEUE_REPOS = UserDocument("review_queue_repos", ReviewQueueRepos)
