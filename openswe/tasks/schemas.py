from typing import Literal, Self
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from openswe.invocation import resolve_invocation_id
from openswe.users import User

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


async def user_for_login(login: str) -> User | None:
    user = await User.for_login("github", login)
    if user is None:
        return None
    identity = next((item for item in user.identities if item.provider == "github"), None)
    return await User.for_identity("github", identity.external_id) if identity is not None else None


class ThreadMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)

    owner_type: Literal["user", "system"] | None = None
    owner_user_id: UUID | None = None
    owner_login: str | None = None
    workspace: str | None = None
    environment: str | None = None
    task_id: UUID | None = None
    sandbox_host_thread_id: str | None = None
    sandbox_id: str | None = None
    sandbox_bridge_client: str | None = None
    sandbox_base_proxy_config: dict[str, JsonValue] | None = None
    visibility: Literal["public", "private"] = "public"
    admin_thread: bool = False
    github_token_repositories: list[str] | None = None
    running_background_tasks: list[str] = Field(default_factory=list)
    source: str = "dashboard"
    title: str | None = None
    repo_owner: str | None = None
    repo_name: str | None = None
    repo: dict[str, JsonValue] | None = None
    repo_explicitly_none: bool = False
    model_selection: Literal["auto", "explicit"] | None = None
    model: str | None = None
    effort: str | None = None

    def json_metadata(self) -> dict[str, JsonValue]:
        return _JSON_OBJECT.validate_python(self.model_dump(mode="json", exclude_unset=True))

    async def owner(self) -> User:
        if self.owner_type != "user":
            raise PermissionError("Task operations require a user-owned thread")
        if self.owner_user_id is not None:
            user = await User.get(self.owner_user_id)
        else:
            user = await user_for_login(self.owner_login or "")
        if user is None:
            raise PermissionError("The thread owner needs an Open SWE account")
        return user


class Thread(BaseModel):
    metadata: ThreadMetadata = Field(default_factory=ThreadMetadata)
    status: str | None = None


class ContentBlock(BaseModel):
    text: str | None = None


class Message(BaseModel):
    role: str = Field(default="", validation_alias=AliasChoices("type", "role"))
    content: str | list[ContentBlock | str] = ""
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list)

    @property
    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "\n".join(
            block.text for block in self.content if isinstance(block, ContentBlock) and block.text
        )


class ThreadState(BaseModel):
    messages: list[Message] = Field(default_factory=list)

    def answer(self, limit: int) -> str:
        for message in reversed(self.messages):
            if message.role in {"human", "user"}:
                break
            if message.role in {"ai", "assistant"} and not message.tool_calls:
                if answer := message.text.strip():
                    return answer[:limit]
        return ""


class RunMetadata(BaseModel):
    invocation_id: str | None = None
    prepare_run_id: str | None = None

    def invocation(self) -> str | None:
        return resolve_invocation_id(self.model_dump(exclude_unset=True))


class RunError(BaseModel):
    error: str = ""
    message: str = ""


class RunPayload(BaseModel):
    metadata: RunMetadata = Field(default_factory=RunMetadata)
    values: ThreadState | None = None
    error: RunError | str | None = None

    @classmethod
    def parse(cls, value: object) -> Self:
        return cls.model_validate(value)

    def failure(self, status: str, limit: int) -> str:
        error = self.error
        detail = (
            ": ".join(part for part in (error.error, error.message) if part)
            if isinstance(error, RunError)
            else error or ""
        )
        return (detail.strip() or f"Worker invocation ended with status {status}.")[:limit]


class Run(BaseModel):
    run_id: str
    status: str
    metadata: RunMetadata = Field(default_factory=RunMetadata)


class StateSnapshot(BaseModel):
    values: ThreadState = Field(default_factory=ThreadState)
