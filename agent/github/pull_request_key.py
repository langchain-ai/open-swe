from typing import Self

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema


class PullRequestKey(str):
    """One pull request as ``owner/repo/number``, lowercased as GitHub matches it."""

    __slots__ = ()

    @classmethod
    def of(cls, owner: str, repo: str, number: int) -> Self:
        return cls(f"{owner}/{repo}/{number}".lower())

    @classmethod
    def parse(cls, raw: str) -> Self | None:
        owner, _, rest = raw.partition("/")
        repo, _, number = rest.partition("/")
        if not owner or not repo or not number.isdigit():
            return None
        return cls.of(owner, repo, int(number))

    @property
    def repo_full_name(self) -> str:
        return self.rsplit("/", 1)[0]

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: object, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())
