"""The Incidents policy, kept as the instance settings record ``incidents``.

It sits beside the instance record rather than in it: one Slack installation
serves the whole instance, so no workspace overrides it, and the general
settings form replaces its own record wholesale on every save.
"""

from datetime import datetime

from sqlalchemy import select

from openswe.dashboard.settings_records import INSTANCE_SETTINGS, SettingsRecord
from openswe.database import postgres
from openswe.incidents.models import IncidentPolicy
from openswe.ui_invalidations import Topic

POLICY_KEY = "incidents"


class IncidentPolicies:
    """The one Incidents policy."""

    async def get(self) -> IncidentPolicy | None:
        value = await INSTANCE_SETTINGS.get(POLICY_KEY)
        return None if value is None else IncidentPolicy.model_validate(value)

    async def put(self, policy: IncidentPolicy) -> IncidentPolicy:
        await INSTANCE_SETTINGS.put(POLICY_KEY, policy.model_dump(mode="json"))
        await Topic.INCIDENT_SETTINGS.invalidate()
        return policy

    async def written_at(self) -> datetime | None:
        async with postgres.session() as session:
            return await session.scalar(
                select(SettingsRecord.updated_at).where(
                    SettingsRecord.tier == INSTANCE_SETTINGS.tier,
                    SettingsRecord.key == POLICY_KEY,
                )
            )
