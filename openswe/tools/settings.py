"""Structured settings access through the dashboard's validated writers."""

from collections.abc import Mapping
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from openswe.audit_logs.tools import audit_tool
from openswe.dashboard.agent_instructions import AGENT_INSTRUCTIONS, AgentInstructionsUpdate
from openswe.dashboard.execution_defaults import EXECUTION_DEFAULTS, ExecutionDefaults
from openswe.dashboard.feature_flags import feature_flag_names
from openswe.dashboard.profiles import ProfileUpdate, get_profile, upsert_profile
from openswe.dashboard.repo_access import require_repo_access_for_user
from openswe.dashboard.settings import (
    SettingsUpdate,
    upsert_instance_settings,
)
from openswe.dashboard.settings_records import INSTANCE_SETTINGS
from openswe.dashboard.user_instructions import (
    UserInstructionsUpdate,
    get_user_instructions,
    set_user_instructions,
)
from openswe.dashboard.user_preferences import (
    UserPreferencesUpdate,
    get_user_preferences,
    set_user_preferences,
)
from openswe.github.repo_names import RepoFullName, normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.plugins.records import PluginApps
from openswe.review.approvals import ApprovalPolicy
from openswe.review.enabled_repos import set_review_repo_enabled
from openswe.review.styles import REVIEW_STYLES, ReviewStylePromptUpdate, effective_approval_mode
from openswe.tools.access import Policy, access, ack
from openswe.tools.admin_gate import actor_is_admin, configurable
from openswe.tools.mcp_exposure import expose_mcp
from openswe.users import User, UserPreferencesPatch
from openswe.utils.json_types import JsonObject, as_json_object

_READ = Policy(trusted="private", actor="owner")
_WRITE = Policy(trusted="private", actor="owner", sole=ack("resource", "scope"), direct=True)
_ADMIN_READ = Policy(trusted="admin_surface", actor="admin")
_ADMIN_WRITE = Policy(
    trusted="admin_surface", actor="admin", sole=ack("resource", "scope", "repository")
)


class RepositoryReviewUpdate(BaseModel):
    enabled: bool


class SettingsResource(StrEnum):
    INSTANCE = "instance"
    EXECUTION = "execution"
    PROFILE = "profile"
    PREFERENCES = "preferences"
    USER_PREFERENCES = "user_preferences"
    INSTRUCTIONS = "instructions"
    FEATURE_FLAGS = "feature_flags"
    REVIEW_SHADOW = "review_shadow"
    REVIEW_APPS = "review_apps"
    REPOSITORY_INSTRUCTIONS = "repository_instructions"
    REPOSITORY_STYLE = "repository_style"
    REPOSITORY_REVIEWS = "repository_reviews"
    REPOSITORY_APPROVAL = "repository_approval"

    @property
    def personal(self) -> bool:
        return self in (self.PROFILE, self.PREFERENCES, self.USER_PREFERENCES, self.INSTRUCTIONS)

    @property
    def repository_scoped(self) -> bool:
        return self in (
            self.REPOSITORY_REVIEWS,
            self.REPOSITORY_APPROVAL,
            self.REPOSITORY_STYLE,
            self.REPOSITORY_INSTRUCTIONS,
        )

    @property
    def model(self) -> type[BaseModel]:
        return {
            self.EXECUTION: ExecutionDefaults,
            self.PROFILE: ProfileUpdate,
            self.PREFERENCES: UserPreferencesUpdate,
            self.USER_PREFERENCES: UserPreferencesPatch,
            self.INSTRUCTIONS: UserInstructionsUpdate,
            self.REPOSITORY_INSTRUCTIONS: AgentInstructionsUpdate,
            self.REPOSITORY_STYLE: ReviewStylePromptUpdate,
            self.REPOSITORY_REVIEWS: RepositoryReviewUpdate,
            self.REPOSITORY_APPROVAL: ReviewStylePromptUpdate,
        }.get(self, SettingsUpdate)

    @property
    def fields(self) -> frozenset[str]:
        match self:
            case self.FEATURE_FLAGS:
                return feature_flag_names(SettingsUpdate)
            case self.REVIEW_SHADOW:
                return frozenset(
                    (
                        "review_shadow_percent",
                        "review_shadow_model",
                        "review_shadow_reasoning_effort",
                    )
                )
            case self.REVIEW_APPS:
                return frozenset(("review_apps", "review_app_preview_checks"))
            case self.REPOSITORY_APPROVAL:
                return frozenset(("approval_mode",))
            case self.USER_PREFERENCES:
                # Robin's own proactive-offer bookkeeping, not a person's choice.
                return frozenset(self.model.model_fields) - {"proactive_offered_at"}
            case _:
                return frozenset(self.model.model_fields)

    def schema(self) -> dict[str, object]:
        schema = self.model.model_json_schema()
        schema["properties"] = {
            key: value for key, value in schema["properties"].items() if key in self.fields
        }
        schema["required"] = [key for key in schema.get("required", []) if key in self.fields]
        schema["additionalProperties"] = False
        return schema

    @staticmethod
    def read_policy(arguments: Mapping[str, object]) -> Policy:
        resource = SettingsResource(str(arguments["resource"]))
        return _READ if resource.personal and arguments.get("user_id") is None else _ADMIN_READ

    @staticmethod
    def write_policy(arguments: Mapping[str, object]) -> Policy:
        resource = SettingsResource(str(arguments["resource"]))
        return _WRITE if resource.personal and arguments.get("user_id") is None else _ADMIN_WRITE

    async def person(self, user_id: UUID | None) -> User:
        cfg = configurable()
        if not await actor_is_admin(cfg):
            if user_id is not None or not cfg.personal_settings_tools_enabled:
                raise ValueError(
                    "This run must enable personal_settings_tools_enabled for personal structured settings access"
                )
        person = (
            await User.get(user_id)
            if user_id
            else await User.for_login(
                "github",
                cfg.mcp_caller or (cfg.github_login if cfg.github_linked else cfg.sender_id) or "",
            )
        )
        if person is None:
            raise ValueError("No authenticated Robin user found")
        return person

    async def personal_values(self, person: User) -> JsonObject:
        login = f"user:{person.id}"
        match self:
            case self.PROFILE:
                value = {
                    **(await get_profile(login) or {}),
                    **person.typed_preferences.model_dump(mode="json"),
                }
            case self.PREFERENCES:
                value = await get_user_preferences(login, strict=True)
            case self.USER_PREFERENCES:
                value = person.typed_preferences.model_dump(mode="json")
            case self.INSTRUCTIONS:
                value = await get_user_instructions(login) or {"instructions": ""}
            case _:
                raise ValueError("Not a personal settings resource")
        return as_json_object({key: value[key] for key in self.fields if key in value})

    def validate(self, values: JsonObject) -> BaseModel:
        return self.model.model_validate(values, strict=True)

    async def patch_personal(self, person: User, patch: JsonObject) -> None:
        cfg = configurable()
        current = await self.personal_values(person)
        update = self.validate({**current, **patch})
        login = f"user:{person.id}"
        match self:
            case self.PROFILE:
                profile = ProfileUpdate.model_validate(update.model_dump())
                preferences = UserPreferencesPatch.model_validate(
                    {
                        key: value
                        for key, value in patch.items()
                        if key in UserPreferencesPatch.model_fields
                    },
                    strict=True,
                )
                await person.merge_preferences(preferences)
                await upsert_profile(login, person.email or "", profile)
            case self.PREFERENCES:
                await set_user_preferences(
                    login, UserPreferencesUpdate.model_validate(update.model_dump())
                )
            case self.USER_PREFERENCES:
                preferences = UserPreferencesPatch.model_validate(patch, strict=True)
                await person.merge_preferences(preferences)
            case self.INSTRUCTIONS:
                instructions = UserInstructionsUpdate.model_validate(update.model_dump())
                await set_user_instructions(
                    login,
                    instructions.instructions,
                    updated_by=cfg.mcp_caller
                    or (cfg.github_login if cfg.github_linked else cfg.sender_id)
                    or "",
                )

    @staticmethod
    async def validate_apps(update: SettingsUpdate) -> None:
        pinned = {UUID(item["app"]): UUID(item["release"]) for item in update.review_apps or []}
        releases = await PluginApps.releases(pinned)
        if len(releases) != len(pinned) or any(item.manifest.review is None for item in releases):
            raise ValueError("Each app must pin its own released version with a review step")

    @staticmethod
    async def repository_target(repository: str) -> tuple[RepoFullName, str]:
        name = normalize_repo_full_name(repository).lower()
        login = configurable().github_login
        if not login:
            raise ValueError("An authenticated requester is required")
        token = await require_repo_access_for_user(login, name)
        return RepoFullName(name), token

    async def read_repository(self, repository: str) -> dict[str, object]:
        name, token = await self.repository_target(repository)
        result: dict[str, object] = {"scope": "repository", "repository": name}
        if self == self.REPOSITORY_REVIEWS:
            row = await Repository.get(name)
            return {
                **result,
                "values": {"enabled": row is not None and row.review_enabled is True},
                "version": row.review_version if row else "missing",
            }
        if self == self.REPOSITORY_INSTRUCTIONS:
            record = await AGENT_INSTRUCTIONS.get(name)
            return {**result, "values": {"instructions": record.instructions if record else ""}}
        style = await REVIEW_STYLES.get(name)
        owner, _, repo = name.partition("/")
        policy = await ApprovalPolicy.load(owner, repo, token=token)
        return {
            **result,
            "values": (
                {
                    "custom_prompt": style.custom_prompt if style else "",
                    "approval_mode": style.approval_mode if style else None,
                }
                if self == self.REPOSITORY_STYLE
                else {"approval_mode": style.approval_mode if style else None}
            ),
            "version": (style.updated_at or "unversioned") if style else "missing",
            "effective_mode": effective_approval_mode(style),
            "approval_criteria_on_default_branch": policy.configured,
        }

    async def patch_repository(
        self, repository: str, patch: JsonObject, expected_version: str
    ) -> dict[str, object]:
        name, _ = await self.repository_target(repository)
        if not expected_version or not patch or patch.keys() - self.fields:
            raise ValueError("Provide a version and only fields listed in this resource's schema")
        if self == self.REPOSITORY_REVIEWS:
            update = RepositoryReviewUpdate.model_validate(patch, strict=True)
            await set_review_repo_enabled(name, update.enabled, expected_version=expected_version)
        else:
            approval_update = ReviewStylePromptUpdate.model_validate(patch, strict=True)
            await REVIEW_STYLES.update_prompts(
                name, approval_update, expected_version=expected_version
            )
        return {"resource": self.value, "scope": "repository", "repository": name}


@expose_mcp()
@access(_READ, per_call=SettingsResource.read_policy)
async def read_settings(
    resource: SettingsResource,
    schema_only: bool = False,
    repository: str | None = None,
    user_id: UUID | None = None,
) -> dict[str, object]:
    """Read any typed settings resource, with schema discovery and audience-aware authorization."""
    selected = SettingsResource(resource)
    if repository is not None and not selected.repository_scoped:
        raise ValueError("Only repository resources accept repository")
    if user_id is not None and not selected.personal:
        raise ValueError("Only personal resources accept user_id")
    person = await selected.person(user_id) if selected.personal else None
    result: dict[str, object] = {"resource": selected.value, "schema": selected.schema()}
    if schema_only:
        return result
    if selected.repository_scoped:
        if repository is None:
            raise ValueError("A repository is required")
        return {**result, **await selected.read_repository(repository)}
    if person is not None:
        return {
            **result,
            "scope": "personal",
            "user_id": str(person.id),
            "values": await selected.personal_values(person),
        }
    store = EXECUTION_DEFAULTS.records if selected == selected.EXECUTION else INSTANCE_SETTINGS
    snapshot = await store.snapshot()
    return {
        **result,
        "scope": "execution" if selected == selected.EXECUTION else "instance",
        "values": {key: snapshot.value.get(key) for key in sorted(selected.fields)},
        "version": snapshot.version,
    }


@expose_mcp()
@audit_tool()
@access(
    _WRITE,
    per_call=SettingsResource.write_policy,
    validation_error="Invalid settings patch; inspect the resource privately",
)
async def patch_settings(
    resource: SettingsResource,
    patch: JsonObject,
    expected_version: str | None = None,
    repository: str | None = None,
    user_id: UUID | None = None,
) -> dict[str, object]:
    """Patch any typed settings resource through its validated settings writer."""
    selected = SettingsResource(resource)
    if not patch or patch.keys() - selected.fields:
        raise ValueError("Provide only nonempty fields listed in the resource schema")
    if repository is not None and not selected.repository_scoped:
        raise ValueError("Only repository resources accept repository")
    if user_id is not None and not selected.personal:
        raise ValueError("Only personal resources accept user_id")
    if selected.personal:
        if expected_version is not None:
            raise ValueError("Personal resources use validated partial writes, not version tokens")
        await selected.patch_personal(await selected.person(user_id), patch)
        return {"ok": True, "resource": selected.value, "scope": "personal"}
    if selected == selected.REPOSITORY_INSTRUCTIONS:
        if repository is None or expected_version is not None:
            raise ValueError("Repository instructions require repository and no version token")
        name, _ = await selected.repository_target(repository)
        instructions = AgentInstructionsUpdate.model_validate(patch, strict=True)
        await AGENT_INSTRUCTIONS.set_instructions(name, instructions.instructions)
        return {"ok": True, "resource": selected.value, "scope": "repository", "repository": name}
    if not expected_version:
        raise ValueError("Read the resource privately and provide expected_version")
    if selected.repository_scoped:
        if repository is None:
            raise ValueError("A repository is required")
        return await selected.patch_repository(repository, patch, expected_version)
    if selected == selected.EXECUTION:
        update = ExecutionDefaults.model_validate(patch, strict=True)
        await EXECUTION_DEFAULTS.update(update, expected_version=expected_version)
        return {"ok": True, "resource": selected.value, "scope": "execution"}
    snapshot = await INSTANCE_SETTINGS.snapshot()
    if snapshot.version != expected_version:
        raise ValueError("Settings changed; read the resource privately again before retrying")
    current = {
        key: value for key, value in snapshot.value.items() if key in SettingsUpdate.model_fields
    }
    update = SettingsUpdate.model_validate(selected.validate({**current, **patch}).model_dump())
    if "review_apps" in patch:
        await selected.validate_apps(update)
    await upsert_instance_settings(update, expected_version=expected_version)
    return {"ok": True, "resource": selected.value, "scope": "instance"}
