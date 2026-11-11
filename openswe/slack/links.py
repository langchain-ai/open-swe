"""Workspace-targeted Slack navigation without ambiguous fallback URLs."""

import re
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlsplit

from openswe.utils.dashboard_links import dashboard_api_base_url


@dataclass(frozen=True)
class SlackLink:
    channel_id: str
    message_ts: str = ""
    thread_ts: str = ""
    team_id: str = ""

    @property
    def url(self) -> str:
        params = {"channel": self.channel_id}
        if self.message_ts:
            params["message_ts"] = self.message_ts
        if self.thread_ts:
            params["thread_ts"] = self.thread_ts
        return f"{dashboard_api_base_url()}/dashboard/api/slack/open?{urlencode(params)}"

    @property
    def channel_url(self) -> str:
        if not self.channel_id.strip() or not self.team_id.strip():
            return self.url
        return f"https://slack.com/app_redirect?{urlencode({'channel': self.channel_id.strip(), 'team': self.team_id.strip()})}"

    def in_workspace(self, workspace_url: str) -> str:
        try:
            parsed = urlsplit(workspace_url)
            valid = (
                parsed.scheme == "https"
                and re.fullmatch(r"[a-z0-9][a-z0-9-]*\.slack\.com", parsed.hostname or "")
                and parsed.hostname != "app.slack.com"
                and parsed.username is None
                and parsed.password is None
                and parsed.port in {None, 443}
                and parsed.path in {"", "/"}
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Slack did not return a workspace URL")
        url = f"https://{parsed.hostname}/archives/{quote(self.channel_id, safe='')}"
        if self.message_ts:
            seconds, fraction = self.message_ts.split(".", 1)
            url += f"/p{quote(seconds + fraction.ljust(6, '0'), safe='')}"
        if self.thread_ts:
            url += f"?{urlencode({'thread_ts': self.thread_ts, 'cid': self.channel_id})}"
        return url
