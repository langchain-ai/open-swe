"""Run a Harbor benchmark job inside a LangSmith sandbox, so the laptop can close.

`up` creates a sandbox that never idles out, uploads a Harbor project, and starts
`harbor run` there detached from any connection. `attach` streams its log, and
Ctrl-C only detaches. `status`, `pull` and `down` check on it, copy its results
home and delete it. Harbor itself runs each task in a sandbox of its own
(`-e langsmith`), so the orchestrator only needs a little CPU and disk.
"""

import argparse
import asyncio
import io
import json
import os
import re
import shlex
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import dotenv_values
from langsmith.sandbox import AsyncSandbox, AsyncSandboxClient

HERE = Path(__file__).resolve().parent
# One file per key, named after it, as in the monorepo's other `secrets/` directories.
SECRETS_DIR = HERE / "secrets"
# open-swe's own `.env`, read only for the keys below; never uploaded.
DOTENV = HERE.parent.parent / ".env"
STATE_DIR = Path.home() / ".harbor-runner" / "runs"
RESULTS_DIR = HERE / "jobs"

# Only these keys reach the sandbox, under the names it reads them by: the LangSmith key
# that creates the orchestrator and Harbor's task sandboxes, and what the adapter needs.
FORWARDED = {
    "LANGSMITH_API_KEY_PROD": "LANGSMITH_API_KEY",
    "ROBIN_EVAL_BACKEND_URL": "ROBIN_EVAL_BACKEND_URL",
    "ROBIN_EVAL_API_KEY": "ROBIN_EVAL_API_KEY",
}
DEFAULT_ENDPOINT = "https://api.smith.langchain.com"

WORK = "/work"
PROJECT = f"{WORK}/project"
SECRETS = f"{WORK}/secrets.env"
SCRIPT = f"{WORK}/run.sh"
LOG = f"{WORK}/harbor.log"
EXIT_FILE = f"{WORK}/exit_code"
PID_FILE = f"{WORK}/harbor.pid"

# The uv the orchestrator installs when its image has none, checked against these digests
# (the release's own `.sha256` files) before it runs.
UV_RELEASE = "https://github.com/astral-sh/uv/releases/download/0.9.8"
UV_SHA256 = {
    "x86_64": "950bad4899800ab158db97081b30a601a3ff4237d705756fda8695342cfcc20a",
    "aarch64": "8dc203a27c99f721545adc531279f0486ea3deb77c951a051e6bb9ea1d588a71",
}

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SKIPPED = {".venv", "jobs", "secrets", "__pycache__", ".ruff_cache", ".git", ".pytest_cache"}
DAY = 86_400


class RunnerError(Exception):
    pass


class Config:
    """Keys from the shell, then `secrets/`, then open-swe's `.env`."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    @classmethod
    async def load(cls) -> "Config":
        dotenv = await asyncio.to_thread(dotenv_values, DOTENV)
        values: dict[str, str] = {}
        for key in FORWARDED:
            try:
                secret = (await asyncio.to_thread((SECRETS_DIR / key).read_text)).strip()
            except FileNotFoundError:
                secret = ""
            values[key] = os.environ.get(key) or secret or dotenv.get(key) or ""
        return cls(values)

    @property
    def endpoint(self) -> str:
        return os.environ.get("HARBOR_RUNNER_LANGSMITH_ENDPOINT") or DEFAULT_ENDPOINT

    def forwarded(self, *, personal_key: bool = False) -> dict[str, str]:
        if missing := [key for key, value in self.values.items() if not value]:
            raise RunnerError(
                f"Set {', '.join(missing)} in the shell, {SECRETS_DIR}/<name> (one file per key) or {DOTENV}"
            )
        if not personal_key and not self.values["ROBIN_EVAL_API_KEY"].startswith("lsv2_sk_"):
            # A personal access token would run every task as that person, on their usage.
            raise RunnerError(
                "ROBIN_EVAL_API_KEY must be a LangSmith service key (lsv2_sk_…) from the Robin "
                "deployment's workspace, so tasks run as its bot; --personal-key accepts a personal token"
            )
        return {sandbox: self.values[host] for host, sandbox in FORWARDED.items()} | {
            "LANGSMITH_ENDPOINT": self.endpoint
        }

    def client(self) -> AsyncSandboxClient:
        if not (key := self.values["LANGSMITH_API_KEY_PROD"]):
            raise RunnerError(f"Set LANGSMITH_API_KEY_PROD in the shell or {SECRETS_DIR}")
        return AsyncSandboxClient(api_endpoint=f"{self.endpoint}/api/v2/sandboxes", api_key=key)


class Orchestrator:
    """The sandbox a run's `harbor run` lives in."""

    def __init__(self, sandbox: AsyncSandbox) -> None:
        self.sandbox = sandbox

    async def checked(self, command: str, timeout: int = 120) -> str:
        result = await self.sandbox.run(command, timeout=timeout)
        if result.exit_code != 0:
            raise RunnerError(
                f"`{command}` failed ({result.exit_code}): {(result.stderr or result.stdout).strip()[-800:]}"
            )
        return result.stdout

    @staticmethod
    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        """The project's files, without its virtualenv, results, secrets or any `.env`."""
        parts = Path(info.name).parts
        if any(part in SKIPPED for part in parts) or Path(info.name).name.startswith(".env"):
            return None
        return info

    @staticmethod
    def script(harbor_args: list[str]) -> str:
        command = " ".join(
            shlex.quote(arg) for arg in ["uv", "run", "--locked", "harbor", "run", *harbor_args]
        )
        sums = "\n".join(f"    {arch}) sum={digest} ;;" for arch, digest in UV_SHA256.items())
        return f"""#!/usr/bin/env bash
set -uo pipefail
cd {PROJECT}
export PATH="$HOME/.local/bin:$PATH"
install_uv() {{
  arch=$(uname -m); [ "$arch" = arm64 ] && arch=aarch64
  case "$arch" in
{sums}
    *) echo "uv has no Linux build for $arch" >&2; return 1 ;;
  esac
  build="uv-$arch-unknown-linux-gnu"
  stage=$(mktemp -d)
  curl -fsSL "{UV_RELEASE}/$build.tar.gz" -o "$stage/$build.tar.gz" &&
    echo "$sum  $stage/$build.tar.gz" | sha256sum -c - &&
    tar -xzf "$stage/$build.tar.gz" -C "$stage" &&
    mkdir -p "$HOME/.local/bin" &&
    mv "$stage/$build/uv" "$HOME/.local/bin/uv"
  status=$?
  rm -rf "$stage"
  return $status
}}
{{ command -v uv >/dev/null || install_uv; }} &&
  set -a && . {SECRETS} && set +a &&
  uv sync --locked && {command}
echo $? > {EXIT_FILE}
"""

    async def start(self, project: Path, secrets: dict[str, str], harbor_args: list[str]) -> None:
        await self.checked(f"mkdir -p {PROJECT} && chmod 700 {WORK}")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            await asyncio.to_thread(tar.add, project, arcname=".", filter=self.keep)
        await self.sandbox.write(f"{WORK}/project.tgz", buffer.getvalue(), timeout=300)
        await self.checked(f"tar -xzf {WORK}/project.tgz -C {PROJECT} && rm {WORK}/project.tgz")
        await self.sandbox.write(
            SECRETS, "".join(f"{key}={shlex.quote(value)}\n" for key, value in secrets.items())
        )
        await self.checked(f"chmod 600 {SECRETS}")
        await self.sandbox.write(SCRIPT, self.script(harbor_args))
        # setsid + nohup: Harbor outlives this exec session and every later one.
        await self.checked(
            f"nohup setsid bash {SCRIPT} > {LOG} 2>&1 < /dev/null & echo $! > {PID_FILE}"
        )

    async def follow(self, name: str, lines: int) -> int:
        """Stream the log until Harbor exits and return its exit code; Ctrl-C detaches and
        leaves Harbor running."""
        print(f"Following {name} (Ctrl-C detaches; Harbor keeps running).", file=sys.stderr)
        command = (
            f"tail -n {lines} -F {LOG} & tailer=$!; "
            f"while [ ! -f {EXIT_FILE} ]; do sleep 5; done; sleep 2; kill $tailer; "
            f'echo; echo "harbor exited $(cat {EXIT_FILE})"'
        )
        handle = await self.sandbox.run(
            command, timeout=DAY, wait=False, kill_on_disconnect=True, idle_timeout=-1
        )
        try:
            async for chunk in handle:
                sys.stdout.write(chunk.data)
                sys.stdout.flush()
        except (KeyboardInterrupt, asyncio.CancelledError):
            await handle.kill()
            print(
                f"\nDetached. `uv run harbor-runner attach {name}` resumes following.",
                file=sys.stderr,
            )
            return 0
        return int((await self.checked(f"cat {EXIT_FILE}")).strip())

    async def report(self, job: str) -> str:
        jobs = f"{PROJECT}/jobs/{shlex.quote(job)}"
        return await self.checked(
            f"""
if [ -f {EXIT_FILE} ]; then echo "state: exited $(cat {EXIT_FILE})";
elif kill -0 "$(cat {PID_FILE} 2>/dev/null)" 2>/dev/null; then echo "state: running";
else echo "state: not running (no exit code; see the log)"; fi
echo "trials started: $(ls -d {jobs}/*__*/ 2>/dev/null | wc -l)"
echo "trials finished: $(grep -l '"finished_at": "' {jobs}/*__*/result.json 2>/dev/null | wc -l)"
echo "passed: $(grep -l '"reward": 1.0' {jobs}/*__*/result.json 2>/dev/null | wc -l)"
""",
        )

    async def pull(self, job: str, destination: Path) -> None:
        archive = f"{WORK}/results.tgz"
        await self.checked(
            f"tar -czf {archive} -C {PROJECT}/jobs {shlex.quote(job)} && cp {LOG} {PROJECT}/jobs/{shlex.quote(job)}.log",
            timeout=600,
        )
        data = await self.sandbox.read(archive, timeout=900)
        log = await self.sandbox.read(f"{PROJECT}/jobs/{job}.log", timeout=300)
        await asyncio.to_thread(destination.mkdir, parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            await asyncio.to_thread(tar.extractall, destination, filter="data")
        await asyncio.to_thread((destination / f"{job}.log").write_bytes, log)


class Run:
    """A run this machine started: its sandbox and Harbor job, kept in ~/.harbor-runner."""

    def __init__(self, name: str, record: dict[str, str]) -> None:
        self.name = name
        self.record = record

    @property
    def job(self) -> str:
        return self.record["job"]

    @property
    def path(self) -> Path:
        return STATE_DIR / f"{self.name}.json"

    @classmethod
    async def load(cls, name: str | None) -> "Run":
        runs = await cls.all()
        if name is None:
            if not runs:
                raise RunnerError("No runs yet; start one with `uv run harbor-runner up`")
            return max(runs, key=lambda run: run.record.get("created_at", ""))
        for run in runs:
            if run.name == name:
                return run
        raise RunnerError(f"No run named {name!r}; `uv run harbor-runner ls` lists them")

    @classmethod
    async def all(cls) -> list["Run"]:
        paths = await asyncio.to_thread(lambda: list(STATE_DIR.glob("*.json")))
        return [
            cls(path.stem, json.loads(await asyncio.to_thread(path.read_text))) for path in paths
        ]

    async def save(self) -> None:
        await asyncio.to_thread(STATE_DIR.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(self.path.write_text, json.dumps(self.record, indent=2))

    async def forget(self) -> None:
        await asyncio.to_thread(self.path.unlink, missing_ok=True)

    async def confirm_deletion(self) -> bool:
        """Asks on stdin; anything but `y`, end of input or Ctrl-C keeps the sandbox.

        Reads through the event loop, not `input` in a worker thread, which Ctrl-C can't stop. A
        regular file, which epoll can't watch, is read directly since it never blocks."""
        print(
            f"Delete sandbox {self.name} and everything in it (pull results first)? [y/N] ",
            end="",
            flush=True,
        )
        loop = asyncio.get_running_loop()
        answer: asyncio.Future[str] = loop.create_future()
        try:
            loop.add_reader(
                sys.stdin, lambda: answer.done() or answer.set_result(sys.stdin.readline())
            )
        except PermissionError:
            return (await asyncio.to_thread(sys.stdin.readline)).strip().lower() == "y"
        try:
            return (await answer).strip().lower() == "y"
        finally:
            loop.remove_reader(sys.stdin)

    async def orchestrator(self, client: AsyncSandboxClient) -> Orchestrator:
        return Orchestrator(await client.get_sandbox(self.name))


async def up(args: argparse.Namespace) -> int:
    config = await Config.load()
    secrets = config.forwarded(personal_key=args.personal_key)
    project = Path(args.project).expanduser().resolve()
    if not await asyncio.to_thread((project / args.config).is_file):
        raise RunnerError(f"{project / args.config} not found")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    job = args.job or f"{Path(args.model).name}-{args.effort}-{stamp}".lower().replace(".", "-")
    name = args.name or f"harbor-{job}"
    if not NAME.match(name):
        raise RunnerError(f"Sandbox name {name!r} must be lowercase letters, digits and dashes")
    if any(run.name == name for run in await Run.all()):
        raise RunnerError(f"A run named {name!r} exists; pick another --name or `down` it first")

    harbor_args = [
        "-c",
        args.config,
        "--agent",
        args.agent,
        "-m",
        args.model,
        "--ak",
        f"effort={args.effort}",
        "-n",
        str(args.concurrency),
        "--yes",
        "--job-name",
        job,
        *(["--ak", "personal_key=true"] if args.personal_key else []),
        *args.harbor_args,
    ]
    async with config.client() as client:
        print(f"Creating sandbox {name} …", file=sys.stderr)
        orchestrator = Orchestrator(
            await client.create_sandbox(
                name=name,
                idle_ttl_seconds=0,  # Harbor makes no calls into the sandbox, so idling would stop it.
                persistent=True,
                timeout=180,
                vcpus=args.vcpus,
                mem_bytes=args.mem_gb * 2**30 if args.mem_gb else None,
                fs_capacity_bytes=args.disk_gb * 2**30 if args.disk_gb else None,
                labels={"tool": "harbor-runner", "job": job},
            )
        )
        await Run(
            name,
            {
                "job": job,
                "model": args.model,
                "effort": args.effort,
                "project": str(project),
                "created_at": datetime.now(UTC).isoformat(),
            },
        ).save()
        try:
            await orchestrator.start(project, secrets, harbor_args)
        except Exception:
            print(
                f"Setup failed; the sandbox is still there. `uv run harbor-runner down {name}` deletes it.",
                file=sys.stderr,
            )
            raise
        print(f"Started job {job} in sandbox {name}.", file=sys.stderr)
        if args.detach:
            print(f"`uv run harbor-runner attach {name}` follows it.", file=sys.stderr)
            return 0
        return await orchestrator.follow(name, lines=200)


async def attach(args: argparse.Namespace) -> int:
    run = await Run.load(args.name)
    async with (await Config.load()).client() as client:
        return await (await run.orchestrator(client)).follow(run.name, lines=args.lines)


async def status(args: argparse.Namespace) -> int:
    run = await Run.load(args.name)
    async with (await Config.load()).client() as client:
        report = await (await run.orchestrator(client)).report(run.job)
    print(f"{run.name}: {run.record['model']} at {run.record['effort']}, job {run.job}")
    print(report.strip())
    return 0


async def pull(args: argparse.Namespace) -> int:
    run = await Run.load(args.name)
    destination = Path(args.dest).expanduser() if args.dest else RESULTS_DIR
    async with (await Config.load()).client() as client:
        await (await run.orchestrator(client)).pull(run.job, destination)
    print(f"Results in {destination / run.job}")
    return 0


async def down(args: argparse.Namespace) -> int:
    run = await Run.load(args.name)
    if not args.yes and not await run.confirm_deletion():
        return 1
    async with (await Config.load()).client() as client:
        await client.delete_sandbox(run.name)
    await run.forget()
    print(f"Deleted {run.name}")
    return 0


async def list_runs(args: argparse.Namespace) -> int:
    runs = sorted(await Run.all(), key=lambda run: run.record.get("created_at", ""))
    if not runs:
        print("No runs.")
        return 0
    async with (await Config.load()).client() as client:
        for run in runs:
            try:
                state = (await client.get_sandbox_status(run.name)).status
            except Exception as cause:  # A deleted sandbox still has a local record.
                state = f"unavailable ({type(cause).__name__})"
            print(
                f"{run.name}  {run.record['model']}/{run.record['effort']}  {run.record['created_at'][:16]}  {state}"
            )
    return 0


def main() -> None:
    root = argparse.ArgumentParser(
        prog="harbor-runner",
        description=__doc__.splitlines()[0],
        epilog="`up … -- <args>` passes <args> to `harbor run`.",
    )
    commands = root.add_subparsers(dest="command", required=True)

    start = commands.add_parser("up", help="start a job in a new sandbox, then follow it")
    start.add_argument("model", nargs="?", default="anthropic/claude-opus-5-5")
    start.add_argument("effort", nargs="?", default="medium")
    start.add_argument(
        "--project", default=str(HERE), help="Harbor project to upload (default: this one)"
    )
    start.add_argument("--config", default="job.yaml", help="job config, relative to the project")
    # Harbor applies -m only to an agent named on the command line, not one from the config.
    start.add_argument(
        "--agent", default="harbor_adapter:RobinAgent", help="agent import path or name"
    )
    start.add_argument("-n", "--concurrency", type=int, default=16)
    start.add_argument("--job", help="Harbor job name (default: <model>-<effort>-<time>)")
    start.add_argument("--name", help="sandbox name (default: harbor-<job>)")
    start.add_argument("--vcpus", type=int)
    start.add_argument("--mem-gb", type=int)
    start.add_argument("--disk-gb", type=int)
    start.add_argument("-d", "--detach", action="store_true", help="don't follow the log")
    start.add_argument(
        "--personal-key",
        action="store_true",
        help="accept a personal access token in ROBIN_EVAL_API_KEY: tasks run as you, on your usage",
    )
    start.set_defaults(func=up)

    for name, func, text in (
        ("attach", attach, "follow a run's log"),
        ("status", status, "show a run's progress"),
        ("pull", pull, "copy a run's results here"),
        ("down", down, "delete a run's sandbox"),
    ):
        command = commands.add_parser(name, help=text)
        command.add_argument("name", nargs="?", help="run name (default: the newest)")
        command.set_defaults(func=func)
    commands.choices["attach"].add_argument("--lines", type=int, default=200)
    commands.choices["pull"].add_argument("--dest", help=f"directory (default: {RESULTS_DIR})")
    commands.choices["down"].add_argument("-y", "--yes", action="store_true")

    commands.add_parser("ls", help="list runs").set_defaults(func=list_runs)

    argv = sys.argv[1:]
    # Everything after `--` goes to `harbor run` untouched.
    split = argv.index("--") if "--" in argv else len(argv)
    args = root.parse_args(argv[:split])
    args.harbor_args = argv[split + 1 :]
    try:
        sys.exit(asyncio.run(args.func(args)))
    except RunnerError as error:
        print(f"harbor-runner: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
