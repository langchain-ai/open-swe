"""Launch the reviewer eval detached in a LangSmith sandbox.

The sandbox checks out ``--ref``, runs ``run_eval`` against ``--langgraph-url``,
reports progress to ``/admin/evals``, and stops itself when the eval exits.
API keys never enter the sandbox: its proxy injects them on the wire.

Usage:
    uv run python -m evals.reviewer.launch --langgraph-url https://<deployment> --limit 3
"""

import argparse
import asyncio
import getpass
import shlex
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv

from agent.config import ENV
from agent.sandboxes.providers.langsmith import get_async_sandbox_client
from agent.utils.gateway import gateway_base_url

REPO_URL = "https://github.com/langchain-ai/open-swe"
KEY_PLACEHOLDER = "sandbox-proxy-injected"
LOG_PATH = "/root/reviewer-eval.log"
EVAL_TIMEOUT_SECONDS = 8 * 60 * 60
DELETE_AFTER_STOP_SECONDS = 24 * 60 * 60


def _key_rule(name: str, urls: list[str], key: str) -> dict[str, Any]:
    return {
        "name": name,
        "match_hosts": [urlsplit(url).netloc for url in urls],
        "headers": [{"name": "x-api-key", "type": "opaque", "value": key}],
    }


def _command(ref: str, eval_args: list[str], stop_url: str) -> str:
    script = (
        'export PATH="/root/.local/bin:$PATH"; '
        "command -v uv || curl -LsSf https://astral.sh/uv/install.sh | sh; "
        f"git clone --depth 1 --branch {shlex.quote(ref)} {REPO_URL} /root/open-swe && "
        "cd /root/open-swe && uv sync --locked && "
        f"timeout {EVAL_TIMEOUT_SECONDS} uv run python -m evals.reviewer.run_eval "
        f"{shlex.join(eval_args)}"
    )
    stop = (
        f"curl -fsS -X POST -H 'x-api-key: {KEY_PLACEHOLDER}' "
        f"-H 'content-type: application/json' -d '{{}}' {shlex.quote(stop_url)}"
    )
    return f"({script}) > {LOG_PATH} 2>&1; {stop}"


async def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--langgraph-url", default=ENV.LANGGRAPH_URL.optional())
    ap.add_argument("--ref", default="prod", help="Branch or tag of open-swe to run.")
    args, eval_args = ap.parse_known_args()
    if not args.langgraph_url:
        ap.error("--langgraph-url (or LANGGRAPH_URL) is required")

    api_key = ENV.LANGSMITH_API_KEY.get()
    endpoint = ENV.LANGSMITH_ENDPOINT.get().rstrip("/")
    gateway = gateway_base_url()
    proxy_config = {
        "rules": [
            _key_rule("langsmith", [endpoint, args.langgraph_url], api_key),
            _key_rule("gateway", [gateway], ENV.LANGSMITH_GATEWAY_API_KEY.optional() or api_key),
        ]
    }
    env = {
        "LANGSMITH_API_KEY": KEY_PLACEHOLDER,
        "LANGSMITH_ENDPOINT": endpoint,
        "LANGSMITH_GATEWAY_BASE_URL": gateway,
        "LANGGRAPH_URL": args.langgraph_url,
        "REVIEWER_EVAL_REPORT_STORE": "1",
        "REVIEWER_EVAL_CREATED_BY": getpass.getuser(),
    }
    async with get_async_sandbox_client() as client:
        sandbox = await client.create_sandbox(
            idle_ttl_seconds=0,
            delete_after_stop_seconds=DELETE_AFTER_STOP_SECONDS,
            proxy_config=proxy_config,
            run_config={"env_vars": env},
            timeout=180,
        )
        stop_url = f"{endpoint}/v2/sandboxes/boxes/{sandbox.name}/stop"
        await sandbox.run(
            _command(args.ref, eval_args, stop_url),
            timeout=EVAL_TIMEOUT_SECONDS + 3600,
            idle_timeout=-1,
            wait=False,
        )
    print(f"Started reviewer eval in sandbox {sandbox.name}; log at {LOG_PATH}.")
    print("Watch progress at /admin/evals.")


if __name__ == "__main__":
    asyncio.run(main())
