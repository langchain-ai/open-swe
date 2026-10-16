import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

_OOO = re.compile(r"\b(?:ooo|out[\s-]+of[\s-]+office|vacation|pto|on leave|on holiday)\b", re.I)


class SlackProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    display_name: str = ""
    real_name: str = ""
    status_text: str = ""
    status_emoji: str = ""
    status_expiration: int = 0


class SlackUserInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    real_name: str = ""
    tz: str = ""
    profile: SlackProfile = Field(default_factory=SlackProfile)

    @property
    def display_name(self) -> str:
        return (
            self.profile.display_name.strip()
            or self.profile.real_name.strip()
            or self.real_name.strip()
            or self.name.strip()
        )

    def out_of_office(self, now: datetime) -> bool:
        if re.search(r"\booo\b", self.display_name, re.I):
            return True
        status = self.profile
        if status.status_expiration and status.status_expiration <= now.timestamp():
            return False
        return bool(_OOO.search(status.status_text))
