import asyncio
import subprocess
from pathlib import Path

import pytest
from deepagents.backends.protocol import ExecuteResponse
from pydantic import JsonValue

from openswe.sandboxes.scripts import SandboxScript


class LocalShell:
    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        output, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return ExecuteResponse(output=output.decode(), exit_code=proc.returncode)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _commit(repo: Path, files: dict[str, str]) -> str:
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)
        _git(repo, "add", name)
    _git(repo, "commit", "-qm", "change")
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "user.email", "t@example.com")
    return repo


async def _compare(repo: Path, base: str, head: str, **layout: JsonValue) -> dict[str, JsonValue]:
    script = SandboxScript("import_graph.py", str(repo.parent / "scripts"))
    return await script.run(
        LocalShell(), {"repo": str(repo), "base": base, "head": head, "roots": ["src"], **layout}
    )


async def test_a_shared_package_importing_the_web_layer_breaks_its_rule_and_closes_a_cycle(
    repo: Path,
) -> None:
    base = _commit(
        repo,
        {
            "src/shop/__init__.py": "",
            "src/shop/shared/__init__.py": "",
            "src/shop/shared/logging.py": "import json\n",
            "src/shop/web/__init__.py": "",
            "src/shop/web/flags.py": "from shop.shared.logging import json\n",
        },
    )
    head = _commit(
        repo,
        {"src/shop/shared/logging.py": "import json\n\nfrom shop.web.flags import is_enabled\n"},
    )

    result = await _compare(
        repo,
        base,
        head,
        rules=[
            {"name": "shared stays below web", "source": ["shop.shared"], "forbidden": ["shop.web"]}
        ],
    )

    assert result["module_edges"] == [
        {
            "source": "shop.shared.logging",
            "target": "shop.web.flags",
            "path": "src/shop/shared/logging.py",
            "imports": [{"line": 3, "kind": "top"}],
            "rules": ["shared stays below web"],
        }
    ]
    assert result["package_cycles"] == [
        {
            "nodes": ["shop.shared", "shop.web"],
            "merged": [],
            "joined": ["shop.shared", "shop.web"],
            "new": [],
            "edges": [
                {
                    "source": "shop.shared",
                    "target": "shop.web",
                    "path": ["shop.shared", "shop.web", "shop.shared"],
                }
            ],
            "edge_count": 1,
        }
    ]


async def test_a_function_local_import_closes_a_module_cycle_the_package_view_already_had(
    repo: Path,
) -> None:
    base = _commit(
        repo,
        {
            "src/shop/__init__.py": "",
            "src/shop/orders/__init__.py": "",
            "src/shop/orders/models.py": "from shop.web.schemas import Request\n",
            "src/shop/orders/receipts.py": "def render(order):\n    return str(order)\n",
            "src/shop/web/__init__.py": "",
            "src/shop/web/schemas.py": "Request = dict\n",
            "src/shop/web/routes.py": "from shop.orders import receipts\n",
        },
    )
    head = _commit(
        repo,
        {
            "src/shop/orders/receipts.py": (
                "def render(order):\n    from ..web.routes import can_view\n    return str(order)\n"
            )
        },
    )

    result = await _compare(repo, base, head)

    assert result["package_edges"] == []
    assert result["package_cycles"] == []
    assert result["module_edges"] == [
        {
            "source": "shop.orders.receipts",
            "target": "shop.web.routes",
            "path": "src/shop/orders/receipts.py",
            "imports": [{"line": 2, "kind": "local"}],
            "rules": [],
        }
    ]
    assert result["module_cycles"] == [
        {
            "nodes": ["shop.orders.receipts", "shop.web.routes"],
            "merged": [],
            "joined": ["shop.orders.receipts", "shop.web.routes"],
            "new": [],
            "edges": [
                {
                    "source": "shop.orders.receipts",
                    "target": "shop.web.routes",
                    "path": ["shop.orders.receipts", "shop.web.routes", "shop.orders.receipts"],
                }
            ],
            "edge_count": 1,
        }
    ]


async def test_type_checking_imports_and_edges_inside_an_existing_cycle_change_no_cycle(
    repo: Path,
) -> None:
    base = _commit(
        repo,
        {
            "src/a.py": "import b\n",
            "src/b.py": "import c\n",
            "src/c.py": "import a\n",
            "src/d.py": "",
        },
    )
    head = _commit(
        repo,
        {
            "src/a.py": "import b\nfrom typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    import d\n",
            "src/b.py": "import c\nimport a\n",
            "src/d.py": "import a\n",
        },
    )

    result = await _compare(repo, base, head, package_depth=1)

    assert result["module_edges"] == [
        {
            "source": "a",
            "target": "d",
            "path": "src/a.py",
            "imports": [{"line": 5, "kind": "typing"}],
            "rules": [],
        },
        {
            "source": "b",
            "target": "a",
            "path": "src/b.py",
            "imports": [{"line": 2, "kind": "top"}],
            "rules": [],
        },
        {
            "source": "d",
            "target": "a",
            "path": "src/d.py",
            "imports": [{"line": 1, "kind": "top"}],
            "rules": [],
        },
    ]
    assert result["module_cycles"] == []
    assert result["package_cycles"] == []
