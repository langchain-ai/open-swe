"""User-authenticated pull request label management."""

from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel

from openswe.github.http import GITHUB_API_BASE, github_client, github_request


class PullRequestLabel(BaseModel):
    name: str
    color: str
    description: str | None = None


class PullRequestLabels(BaseModel):
    available: list[PullRequestLabel]
    selected: list[PullRequestLabel]


class LabelChange(BaseModel):
    name: str
    selected: bool


class PullRequestLabelClient:
    def __init__(self, owner: str, repo: str, number: int, token: str):
        self.base = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
        self.number = number
        self.token = token

    async def read(self) -> PullRequestLabels:
        async with github_client(token=self.token) as client:
            available: list[PullRequestLabel] = []
            page = 1
            while True:
                response = await github_request(
                    client, "GET", f"{self.base}/labels", params={"per_page": 100, "page": page}
                )
                if response.is_error:
                    raise HTTPException(response.status_code, "Could not load repository labels")
                labels = [PullRequestLabel.model_validate(label) for label in response.json()]
                available.extend(labels)
                if len(labels) < 100:
                    break
                page += 1
            response = await github_request(client, "GET", f"{self.base}/issues/{self.number}")
            if response.is_error:
                raise HTTPException(response.status_code, "Could not load pull request labels")
            return PullRequestLabels(
                available=available,
                selected=[
                    PullRequestLabel.model_validate(label) for label in response.json()["labels"]
                ],
            )

    async def change(self, change: LabelChange) -> None:
        async with github_client(token=self.token) as client:
            url = f"{self.base}/issues/{self.number}/labels"
            if change.selected:
                response = await github_request(client, "POST", url, json={"labels": [change.name]})
            else:
                response = await github_request(
                    client, "DELETE", f"{url}/{quote(change.name, safe='')}"
                )
            if response.is_error:
                raise HTTPException(
                    response.status_code, "Could not update label; check your GitHub permissions"
                )
