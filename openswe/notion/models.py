"""The parts of Notion API objects and webhook events Open SWE reads.

Every model ignores fields it does not declare: Notion adds fields freely, and a
page must not fail to parse because of a property type this module never uses.
"""

from pydantic import BaseModel, ConfigDict, Field

from openswe.notion.settings import normalize_notion_id


class _NotionModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PersonDetails(_NotionModel):
    email: str | None = None


class NotionUser(_NotionModel):
    id: str
    type: str | None = None
    name: str | None = None
    person: PersonDetails | None = None

    @property
    def is_bot(self) -> bool:
        return self.type == "bot"

    @property
    def email(self) -> str | None:
        return self.person.email if self.person else None


class TextLink(_NotionModel):
    url: str | None = None


class TextContent(_NotionModel):
    content: str = ""
    link: TextLink | None = None


class Mention(_NotionModel):
    type: str = ""
    user: NotionUser | None = None


class RichText(_NotionModel):
    type: str = "text"
    plain_text: str = ""
    href: str | None = None
    text: TextContent | None = None
    mention: Mention | None = None

    @property
    def mentioned_user_id(self) -> str | None:
        mention = self.mention
        if self.type != "mention" or mention is None or mention.type != "user":
            return None
        return mention.user.id if mention.user else None


class SelectOption(_NotionModel):
    id: str | None = None
    name: str = ""


class UniqueId(_NotionModel):
    prefix: str | None = None
    number: int | None = None


class RelationRef(_NotionModel):
    id: str


class DateValue(_NotionModel):
    start: str | None = None
    end: str | None = None


class PropertyValue(_NotionModel):
    """One property of a page; only the member named by ``type`` is populated."""

    id: str = ""
    type: str = ""
    title: list[RichText] | None = None
    rich_text: list[RichText] | None = None
    select: SelectOption | None = None
    status: SelectOption | None = None
    multi_select: list[SelectOption] | None = None
    people: list[NotionUser] | None = None
    url: str | None = None
    relation: list[RelationRef] | None = None
    unique_id: UniqueId | None = None
    date: DateValue | None = None
    checkbox: bool | None = None


class Parent(_NotionModel):
    type: str = ""
    data_source_id: str | None = None
    database_id: str | None = None
    page_id: str | None = None
    block_id: str | None = None


class NotionPage(_NotionModel):
    id: str
    url: str = ""
    parent: Parent = Field(default_factory=Parent)
    properties: dict[str, PropertyValue] = Field(default_factory=dict)
    created_by: NotionUser | None = None
    in_trash: bool = False


class CommentParent(_NotionModel):
    type: str = ""
    page_id: str | None = None
    block_id: str | None = None


class NotionComment(_NotionModel):
    id: str
    discussion_id: str = ""
    parent: CommentParent = Field(default_factory=CommentParent)
    created_by: NotionUser | None = None
    created_time: str = ""
    rich_text: list[RichText] = Field(default_factory=list)

    def mentions(self, user_ids: frozenset[str]) -> bool:
        return any(
            (user_id := item.mentioned_user_id) is not None
            and normalize_notion_id(user_id) in user_ids
            for item in self.rich_text
        )


class FileRef(_NotionModel):
    url: str = ""


class BlockContent(_NotionModel):
    """The type-specific payload shared by the text-like block types."""

    rich_text: list[RichText] = Field(default_factory=list)
    caption: list[RichText] = Field(default_factory=list)
    checked: bool | None = None
    language: str | None = None
    type: str | None = None
    file: FileRef | None = None
    external: FileRef | None = None
    url: str | None = None
    title: str | None = None


class NotionBlock(BaseModel):
    """A block whose type-specific payload lives under a key named by ``type``."""

    model_config = ConfigDict(extra="allow")

    id: str
    type: str = ""
    has_children: bool = False

    def content(self) -> BlockContent:
        raw = (self.model_extra or {}).get(self.type)
        if isinstance(raw, dict):
            return BlockContent.model_validate(raw)
        return BlockContent()


class SchemaOptions(_NotionModel):
    options: list[SelectOption] = Field(default_factory=list)


class SchemaProperty(_NotionModel):
    id: str = ""
    name: str = ""
    type: str = ""
    status: SchemaOptions | None = None
    select: SchemaOptions | None = None
    multi_select: SchemaOptions | None = None

    def option_names(self) -> set[str]:
        options = self.status or self.select or self.multi_select
        return {option.name for option in options.options} if options else set()


class PageMarkdown(_NotionModel):
    markdown: str = ""
    truncated: bool = False


class NotionDataSource(_NotionModel):
    id: str
    properties: dict[str, SchemaProperty] = Field(default_factory=dict)


class PageList(_NotionModel):
    results: list[NotionPage] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None


class BlockList(_NotionModel):
    results: list[NotionBlock] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None


class CommentList(_NotionModel):
    results: list[NotionComment] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: str | None = None


class WebhookAuthor(_NotionModel):
    id: str
    type: str = ""


class WebhookEntity(_NotionModel):
    id: str
    type: str = ""


class WebhookParent(_NotionModel):
    id: str = ""
    type: str = ""


class WebhookEventData(_NotionModel):
    parent: WebhookParent | None = None
    page_id: str | None = None
    updated_properties: list[str] = Field(default_factory=list)


class WebhookEvent(_NotionModel):
    id: str = ""
    type: str
    entity: WebhookEntity
    authors: list[WebhookAuthor] = Field(default_factory=list)
    data: WebhookEventData = Field(default_factory=WebhookEventData)

    def authored_by_agent(self, agent_user_ids: frozenset[str]) -> bool:
        """Whether only integrations or the agent's own member caused this event.

        Writes made with the agent's personal access token are authored by its
        member (or the token's bot), so they are the agent's own too.
        """
        return bool(self.authors) and all(
            author.type == "bot" or normalize_notion_id(author.id) in agent_user_ids
            for author in self.authors
        )

    @property
    def person_author_id(self) -> str | None:
        return next((author.id for author in self.authors if author.type == "person"), None)
