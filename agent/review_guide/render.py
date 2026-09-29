"""Jinja rendering of the guide's Slack messages, so code is quoted from the checkout, never retyped."""

import posixpath

from deepagents.backends.protocol import SandboxBackendProtocol
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from agent.review_guide import git

_ENV = SandboxedEnvironment(enable_async=True, autoescape=False, undefined=StrictUndefined)


class RenderError(ValueError):
    """The message template could not be rendered."""


def _fenced(body: str, language: str = "") -> str:
    return f"```{language}\n{body.rstrip()}\n```" if body.strip() else "_(no changes)_"


class MessageRenderer:
    def __init__(self, backend: SandboxBackendProtocol, repo_dir: str) -> None:
        self._backend = backend
        self._repo_dir = repo_dir
        self.quoted_chunk = False

    async def staged(self) -> str:
        self.quoted_chunk = True
        return _fenced(await git.staged_diff(self._backend, self._repo_dir), "diff")

    async def stat(self) -> str:
        self.quoted_chunk = True
        return _fenced(await git.staged_stat(self._backend, self._repo_dir))

    async def diff(self, path: str) -> str:
        return _fenced(await git.unstaged_diff(self._backend, self._repo_dir, path), "diff")

    async def code(self, path: str, start: int = 1, end: int | None = None) -> str:
        lines = (await git.head_file(self._backend, self._repo_dir, path)).splitlines()
        first = max(start, 1)
        last = min(end or len(lines), len(lines))
        language = posixpath.splitext(path)[1].removeprefix(".")
        return _fenced("\n".join(lines[first - 1 : last]), language)

    async def render(self, template: str) -> str:
        try:
            return await _ENV.from_string(template).render_async(
                staged=self.staged, stat=self.stat, diff=self.diff, code=self.code
            )
        except (TemplateError, git.GuideGitError) as exc:
            raise RenderError(str(exc)) from exc
