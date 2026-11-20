"""Harbor adapter that runs Robin on Terminal-Bench tasks by attaching each task's sandbox to Robin.

Harbor creates the task's LangSmith sandbox with the `robin.external=true` label, and the `robin`
CLI, running beside Harbor, starts a Robin thread with that sandbox attached (`--sandbox`). Robin's
backend then runs every command in the task's sandbox itself, where the task's verifier grades it
afterwards. Robin itself runs unchanged on the backend the CLI points at.

Nothing of Robin's enters the task's sandbox: no CLI, no key, and no proxy rule, so the task's
network policy is the one Harbor set.
"""

import asyncio
import io
import os
import platform
import re
import shlex
import signal
import tarfile
import tempfile
import urllib.request
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, override
from urllib.parse import urlparse

from harbor.agents.base import BaseAgent
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.environments.langsmith import LangSmithEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field


class RobinSandbox(LangSmithEnvironment):
    """Harbor's LangSmith environment, with the label Robin requires before it attaches a sandbox
    it didn't create. The sandbox API only takes labels at creation."""

    LABELS: ClassVar[dict[str, str]] = {"robin.external": "true"}

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        if self._compose_mode:
            # Robin runs commands in the sandbox itself, not in a compose service inside it.
            raise RuntimeError("Robin can't run a docker-compose task")

    @property
    def sandbox_name(self) -> str:
        return self._sandbox_name

    @property
    def env_names(self) -> list[str]:
        """The variables Harbor sets on each of its own commands (the task's `[environment].env`
        and the job's), which Robin's commands, run by its backend, never get."""
        return sorted(self._startup_env())

    # Harbor 0.24's `_create_sandbox`, plus `labels`; Harbor has no option for them.
    @override
    def _create_sandbox(self, snapshot_name: str | None) -> object:
        payload = self._create_sandbox_payload(snapshot_name)
        client = self._create_sandbox_client()
        try:
            return client.create_sandbox(
                snapshot_id=payload.get("snapshot_id"),
                snapshot_name=payload.get("snapshot_name"),
                name=payload["name"],
                timeout=self._startup_timeout_seconds,
                idle_ttl_seconds=payload["idle_ttl_seconds"],
                delete_after_stop_seconds=payload["delete_after_stop_seconds"],
                vcpus=payload.get("vcpus"),
                mem_bytes=payload.get("mem_bytes"),
                fs_capacity_bytes=payload.get("fs_capacity_bytes"),
                proxy_config=payload.get("proxy_config"),
                labels=self.LABELS,
                headers=self._langsmith_client_headers(),
            )
        finally:
            client.close()


class RobinOptions(AgentOptions):
    version: str | None = Field(
        default=None,
        description="The robin release to run (`desktop-v<version>`); the pinned one when unset.",
    )
    effort: str | None = Field(
        default=None,
        description="Reasoning effort for the job's model; the backend honors --model only with it.",
    )
    sha256: str | None = Field(
        default=None,
        description="SHA-256 of the release tarball for this machine, for a version other than "
        "the pinned one.",
    )
    workdir: str | None = Field(
        default=None,
        description="Directory Robin works in; the image's WORKDIR when unset.",
    )
    personal_key: bool = Field(
        default=False,
        description="Accept a personal access token: every task runs as its owner, on their usage.",
    )


class RobinAgent(BaseAgent):
    """Runs `robin run --sandbox <task sandbox>` once, beside Harbor, with the task's instruction
    as its prompt."""

    options_model = RobinOptions

    RELEASES = "https://github.com/langchain-ai/open-swe/releases/download"
    # The first build with `--sandbox`, which attaches an external sandbox.
    PINNED_VERSION = "0.3.20-nightly.20261119232528"
    # robin-SHA256SUMS of desktop-v0.3.20-nightly.20261119232528.
    PINNED_SHA256: ClassVar[dict[str, str]] = {
        "darwin-arm64": "9d7fd91043ff42499bfbd04095b7e67006951a4747c40f591f3d7494cba3fb31",
        "darwin-x64": "9e97166f677f08600ed3e3fdeb29e722216872c9fcd88c71d261922b14b6ecde",
        "linux-arm64": "2758ab564e48a9a6dd98b99e440caac5d6415dccc7ab89a3875a0728ef5d96e7",
        "linux-x64": "1c9780d067c87b7d90b78ba23dabfffda7e41becbc98794a093bfbb2daec8943",
    }
    ARCHES: ClassVar[dict[str, str]] = {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    # The benchmark's own names, so a shell running it never repoints the person's robin.
    BACKEND_ENV = "ROBIN_EVAL_BACKEND_URL"
    KEY_ENV = "ROBIN_EVAL_API_KEY"
    # Robin runs a LangSmith service key as its workspace's bot, which starts system threads;
    # a personal access token would run every task as that person, on their model usage.
    SERVICE_KEY_PREFIX = "lsv2_sk_"
    SCHEME = re.compile(r"^https?://", re.IGNORECASE)

    STDOUT = "robin.stdout"
    STDERR = "robin.stderr"
    # The CLI prints the thread's URL on stdout once the backend accepted the run.
    THREAD_LINE = re.compile(r"^(https?://\S+/agents/\S+)$", re.MULTILINE)
    # Robin stopped to ask a question: an answer the verifier still grades.
    INTERRUPTED = re.compile(r"^robin: run interrupted;", re.MULTILINE)
    # Long enough for the CLI to cancel the remote run and its workers after SIGINT.
    STOP_GRACE_SEC = 30

    # One download per Harbor process, shared by its trials.
    _binary: ClassVar[dict[str, Path]] = {}
    _download_lock: ClassVar[asyncio.Lock | None] = None

    options: RobinOptions

    @staticmethod
    @override
    def name() -> str:
        return "robin"

    @override
    def version(self) -> str | None:
        return self.release

    @property
    def release(self) -> str:
        return self.options.version or self.PINNED_VERSION

    def key(self) -> str:
        """The key the CLI authenticates with, from the environment `harbor-runner` loads it into."""
        key = os.environ.get(self.KEY_ENV, "")
        if not key:
            raise RuntimeError(f"Set {self.KEY_ENV} in the environment")
        if not self.options.personal_key and not key.startswith(self.SERVICE_KEY_PREFIX):
            raise RuntimeError(
                f"{self.KEY_ENV} must be a LangSmith service key (lsv2_sk_…) from the Robin "
                "deployment's workspace, so tasks run as its bot rather than as a person; "
                "`--ak personal_key=true` accepts a personal access token"
            )
        return key

    def backend(self) -> str:
        """The backend's origin, read as the CLI reads it: a bare host means `https://`. It must be
        HTTPS, so the key never crosses the wire in the clear."""
        given = os.environ.get(self.BACKEND_ENV, "").strip().rstrip("/")
        if not given:
            raise RuntimeError(f"Set {self.BACKEND_ENV} in the environment")
        url = urlparse(given if self.SCHEME.match(given) else f"https://{given}")
        if (
            url.scheme.lower() != "https"
            or not url.hostname
            or url.path
            or url.query
            or url.fragment
        ):
            raise RuntimeError(f"{self.BACKEND_ENV} must be an https:// origin, got {given!r}")
        host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        return f"https://{host}" + (f":{url.port}" if url.port else "")

    def cli_env(self) -> dict[str, str]:
        """Only what the CLI needs, so nothing else from Harbor's environment reaches it."""
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", tempfile.gettempdir()),
            "OPEN_SWE_BACKEND_URL": self.backend(),
            "OPEN_SWE_API_KEY": self.key(),
        }

    @classmethod
    def target(cls) -> str:
        """This machine's build name, as the release names its tarballs."""
        system = platform.system().lower()
        arch = cls.ARCHES.get(platform.machine().lower())
        if system not in ("linux", "darwin") or arch is None:
            raise RuntimeError(f"robin ships no build for {platform.system()} {platform.machine()}")
        return f"{system}-{arch}"

    def fetch(self, url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 (fixed https URL)
            return response.read()

    def expected_sha256(self, base: str, tarball: str) -> str:
        if self.options.sha256:
            return self.options.sha256.removeprefix("sha256:")
        if self.release == self.PINNED_VERSION:
            return self.PINNED_SHA256[self.target()]
        sums = self.fetch(f"{base}/robin-SHA256SUMS").decode()
        for line in sums.splitlines():
            digest, _, name = line.partition("  ")
            if name == tarball:
                return digest
        raise RuntimeError(f"robin-SHA256SUMS of {self.release} lists no {tarball}")

    def download(self, destination: Path) -> None:
        """Fetches the release tarball, checks its digest, and unpacks `robin` into place."""
        tarball = f"robin-{self.target()}.tar.gz"
        base = f"{self.RELEASES}/desktop-v{self.release}"
        data = self.fetch(f"{base}/{tarball}")
        expected = self.expected_sha256(base, tarball)
        if sha256(data).hexdigest() != expected:
            raise RuntimeError(f"{tarball} of {self.release} doesn't match sha256 {expected}")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member = tar.extractfile("robin")
            if member is None:
                raise RuntimeError(f"{tarball} has no robin binary")
            destination.write_bytes(member.read())
        destination.chmod(0o700)

    async def binary(self) -> Path:
        cls = type(self)
        if cls._download_lock is None:
            cls._download_lock = asyncio.Lock()
        async with cls._download_lock:
            if (path := cls._binary.get(self.release)) is None:
                # A fresh owner-only directory, so nothing another user left in the shared
                # temp dir is ever run in place of the checked download.
                staging = await asyncio.to_thread(tempfile.mkdtemp, prefix="robin-cli-")
                path = Path(staging) / "robin"
                await asyncio.to_thread(self.download, path)
                cls._binary[self.release] = path
            return path

    async def robin_model(self) -> str | None:
        """The job's model as Robin names it: `anthropic/claude-x` is `anthropic:claude-x`."""
        if not self.model_name:
            if self.options.effort:
                # Harbor drops `-m` for an agent defined in `-c`'s config; naming the agent on the
                # command line (`--agent harbor_adapter:RobinAgent`) is what applies it.
                raise ValueError(
                    "An effort was set but no model reached the agent; pass "
                    "`--agent harbor_adapter:RobinAgent -m <model>`"
                )
            return None
        if not self.options.effort:
            raise ValueError(
                "Robin ignores a model without an effort; set the agent's `effort` kwarg"
            )
        provider, slash, model = self.model_name.partition("/")
        return f"{provider}:{model}" if slash and ":" not in self.model_name else self.model_name

    @override
    async def setup(self, environment: BaseEnvironment) -> None:
        if not isinstance(environment, RobinSandbox):
            raise RuntimeError(
                "Robin attaches only to `harbor_adapter:RobinSandbox` environments "
                "(job.yaml `environment.import_path`)"
            )
        if names := environment.env_names:
            raise RuntimeError(
                f"The task sets {', '.join(names)} for Harbor's commands, but Robin's commands "
                "wouldn't get them, so it would be graded on a different environment"
            )
        robin = await self.binary()
        # Fails the trial's setup, not its run, when the backend or key is wrong.
        check = await asyncio.create_subprocess_exec(
            str(robin),
            "auth",
            "status",
            env=self.cli_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await check.communicate()
        if check.returncode != 0:
            raise RuntimeError(f"robin auth status failed: {output.decode().strip()[-500:]}")

    async def workdir(self, environment: BaseEnvironment) -> str:
        """The option, or the directory a command starts in: the image's WORKDIR."""
        if self.options.workdir:
            return self.options.workdir
        found = await environment.exec(command="pwd")
        if found.return_code != 0 or not (cwd := (found.stdout or "").strip()).startswith("/"):
            raise RuntimeError(f"Could not read the task's working directory: {found.stderr}")
        return cwd

    async def stop(self, process: asyncio.subprocess.Process) -> None:
        """SIGINT makes the CLI cancel the remote run and its workers; waiting for it keeps them
        from running on while the verifier grades the environment."""
        if process.returncode is not None:
            return
        process.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(process.wait(), self.STOP_GRACE_SEC)
        except TimeoutError:
            process.kill()
            await process.wait()

    @override
    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        assert isinstance(environment, RobinSandbox)
        argv = [
            str(await self.binary()),
            "run",
            "--sandbox",
            environment.sandbox_name,
            "--work-dir",
            await self.workdir(environment),
        ]
        if model := await self.robin_model():
            argv += ["--model", model, "--effort", str(self.options.effort)]
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = self.logs_dir / self.STDOUT
        stderr_path = self.logs_dir / self.STDERR
        self.logger.debug("Running %s", shlex.join(argv))
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = await asyncio.create_subprocess_exec(
                *argv,
                env=self.cli_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
            )
            # The prompt goes on stdin, so it is never an argument a process listing shows.
            try:
                await process.communicate(instruction.encode())
            except asyncio.CancelledError:
                # Harbor's agent timeout cancels this coroutine; stop the remote run first.
                await asyncio.shield(self.stop(process))
                raise
        out = stdout_path.read_text(errors="replace")
        err = stderr_path.read_text(errors="replace")
        thread = self.THREAD_LINE.search(out)
        context.metadata = {
            **(context.metadata or {}),
            "robin_exit_code": process.returncode,
            "robin_thread_url": thread.group(1) if thread else None,
        }
        # Robin finished, or stopped to ask a question: either way an attempt the verifier grades.
        # Anything else (the backend refused the thread, or the run failed) never was one.
        if thread is None or not (process.returncode == 0 or self.INTERRUPTED.search(err)):
            raise RuntimeError(f"robin exited {process.returncode}: {err.strip()[-500:]}")

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """Robin's trace is in LangSmith, linked from the thread; Harbor gets no trajectory."""
