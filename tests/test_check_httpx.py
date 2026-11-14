import difflib
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_httpx.py"


@pytest.mark.parametrize(
    ("source", "added", "expected"),
    [
        ("import httpx\nclient = httpx.AsyncClient()\n", {2}, [2]),
        ("import httpx as h\nclient = h.Client()\n", {2}, [2]),
        ("from httpx import AsyncClient as Client\nclient = Client()\n", {2}, [2]),
        ("import httpx\nFactory = httpx.AsyncClient\nclient = Factory()\n", {2, 3}, [2, 3]),
        ("import httpx\nFactory = httpx.AsyncClient\nclient = Factory()\n", {3}, [3]),
        ("import httpx\nclient = httpx.AsyncClient()\n", {1}, [1]),
        ("from httpx import AsyncClient as Client\nclient = Client()\n", {1}, [1]),
        (
            "from httpx import AsyncHTTPTransport\nFactory = AsyncHTTPTransport\n"
            "Other: object = Factory\ntransport = Other()\n",
            {3},
            [3],
        ),
        ("import httpx\nSdkClient = httpx.AsyncClient\n", {1, 2}, []),
        ("Factory = Other\nOther = Factory\nclient = Factory()\n", {1, 2, 3}, []),
        ("from httpx import (\n    AsyncHTTPTransport,\n)\n", {2}, []),
        ("from httpx import AsyncClient\ndef sdk(client: AsyncClient) -> None: pass\n", {1, 2}, []),
        ("import httpx\ndef sdk(client: httpx.AsyncClient) -> None: pass\n", {2}, []),
        ("import httpx\nclient = httpx.AsyncClient(\n    timeout=5,\n)\n", {3}, []),
        ("import httpx\ntransport = httpx.HTTPTransport()\n", {2}, [2]),
        ("import httpx\nclient = httpx.AsyncClient()\nvalue = 1\n", {3}, []),
        (
            "from httpx import AsyncClient\nimport httpx2\n"
            "def other():\n    AsyncClient = httpx2.AsyncClient\n"
            "client = AsyncClient()\n",
            {5},
            [5],
        ),
        (
            "import httpx\ndef other():\n    import httpx2 as httpx\n"
            "    client = httpx.AsyncClient()\nclient = httpx.AsyncClient()\n",
            {4, 5},
            [5],
        ),
        (
            "from httpx import AsyncClient\ndef other(AsyncClient):\n"
            "    client = AsyncClient()\nclient = AsyncClient()\n",
            {3, 4},
            [4],
        ),
        (
            "from httpx import (\n    AsyncClient,\n    ConnectError,\n)\nclient = AsyncClient()\n",
            {3},
            [],
        ),
        (
            "from httpx import (\n    AsyncClient,\n    ConnectError,\n)\nclient = AsyncClient()\n",
            {2},
            [2],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx.AsyncClient\n"
            "if use_httpx2:\n    Client = httpx2.AsyncClient\nclient = Client()\n",
            {6},
            [6],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx.AsyncClient\n"
            "if use_httpx2:\n    Client = httpx2.AsyncClient\n    client = Client()\n",
            {6},
            [],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx.AsyncClient\n"
            "if other:\n    Client = httpx2.AsyncClient\n"
            "else:\n    Client = httpx2.Client\nclient = Client()\n",
            {8},
            [],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx.AsyncClient\n"
            "if flag:\n    Client = httpx2.AsyncClient\n"
            "    def create():\n        return Client()\n",
            {7},
            [],
        ),
        (
            "import httpx\nimport httpx2\ndef outer():\n"
            "    Client = httpx.AsyncClient\n    if flag:\n"
            "        Client = httpx2.AsyncClient\n"
            "        def create():\n            return Client()\n",
            {8},
            [],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx.AsyncClient\n"
            "if flag:\n    Client = httpx2.AsyncClient\n"
            "    def create():\n        return Client()\n"
            "Client = httpx.AsyncClient\n",
            {7},
            [7],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx2.AsyncClient\n"
            "for flag in (True, False):\n    if flag:\n"
            "        def create():\n            return Client()\n"
            "    else:\n        Client = httpx.AsyncClient\ncreate()\n",
            {7},
            [7],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx2.AsyncClient\n"
            "while running:\n    if flag:\n"
            "        def create():\n            return Client()\n"
            "    else:\n        Client = httpx.AsyncClient\ncreate()\n",
            {7},
            [7],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx.AsyncClient\n"
            "for flag in flags:\n    if flag:\n"
            "        Client = httpx2.AsyncClient\n        client = Client()\n",
            {7},
            [],
        ),
        (
            "import httpx\nClient = httpx.AsyncClient\nfor flag in flags:\n"
            "    if flag:\n        def Client():\n            pass\nclient = Client()\n",
            {7},
            [7],
        ),
        (
            "import httpx\nClient = httpx.AsyncClient\nfor flag in flags:\n"
            "    if flag:\n        async def Client():\n            pass\nclient = Client()\n",
            {7},
            [7],
        ),
        (
            "import httpx\nimport httpx2\nfor flag in flags:\n    if flag:\n"
            "        Client = httpx2.AsyncClient\n"
            "        def create():\n            return Client()\n        create()\n"
            "    else:\n        Client = httpx.AsyncClient\n",
            {7},
            [],
        ),
        (
            "import httpx\nimport httpx2\nfor flag in flags:\n    if flag:\n"
            "        Client = httpx2.AsyncClient\n"
            "        def create():\n            return Client()\n        create()\n"
            "    else:\n        Client = httpx.AsyncClient\ncreate()\n",
            {7},
            [7],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx2.AsyncClient\n"
            "for flag in (False, True):\n    if flag:\n"
            "        def create():\n            return Client()\n        create()\n"
            "    else:\n        Client = httpx.AsyncClient\n",
            {7},
            [7],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx2.AsyncClient\n"
            "for flag in (False, True):\n    if flag:\n        client = Client()\n"
            "    else:\n        Client = httpx.AsyncClient\n",
            {6},
            [6],
        ),
        (
            "import httpx\nimport httpx2\nClient = httpx.AsyncClient\n"
            "for flag in (False, True):\n    if flag:\n"
            "        Client = httpx2.AsyncClient\n    client = Client()\n",
            {7},
            [7],
        ),
        (
            "import httpx\ntransport = httpx.AsyncHTTPTransport()  "
            "# httpx-sdk: google-genai requires an httpx transport\n"
            "client = httpx.AsyncClient()\n",
            {2, 3},
            [3],
        ),
        ("import httpx\nclient = httpx.Client()  # httpx-sdk:\n", {2}, [2]),
        (
            'import httpx\nclient = httpx.Client(note="# httpx-sdk: not a comment")\n',
            {2},
            [2],
        ),
        ("import httpx2\nclient = httpx2.AsyncClient()\n", {1, 2}, []),
        ("import httpx\nraise httpx.ConnectError('SDK failed')\n", {1, 2}, []),
        ('text = "httpx.AsyncClient()"\n', {1}, []),
    ],
)
def test_checks_added_client_references(
    tmp_path: Path, source: str, added: set[int], expected: list[int]
) -> None:
    from scripts.check_httpx import HttpxCheck

    path = tmp_path / "client.py"
    path.write_text(source)
    assert HttpxCheck(path).client_lines(added) == expected


@pytest.mark.parametrize("client", ["httpx", "httpx2"])
def test_cli_checks_relative_diff_and_returns_failure(tmp_path: Path, client: str) -> None:
    path = tmp_path / "client.py"
    path.write_text(f"import {client}\nclient = {client}.AsyncClient()\n")
    diff = f"""diff --git a/client.py b/client.py
--- /dev/null
+++ b/client.py
@@ -0,0 +1,2 @@
+import {client}
+client = {client}.AsyncClient()
"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=diff,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == (1 if client == "httpx" else 0)
    assert ("client.py:2:" in result.stderr) == (client == "httpx")


@pytest.mark.parametrize("deleted_first", [True, False])
def test_cli_ignores_deleted_files(tmp_path: Path, deleted_first: bool) -> None:
    (tmp_path / "client.py").write_text("import httpx\nclient = httpx.AsyncClient()\n")
    changed = """--- a/client.py
+++ b/client.py
@@ -3 +3 @@
-old = 1
+new = 2
"""
    deleted = """--- a/deleted.py
+++ /dev/null
@@ -1 +0,0 @@
-import httpx
"""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=deleted + changed if deleted_first else changed + deleted,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("before", "after", "call_added", "expected"),
    [
        ("AsyncClient", "AsyncClient, ConnectError", False, 0),
        ("AsyncClient", "AsyncClient, ConnectError", True, 1),
        ("ConnectError", "AsyncClient, ConnectError", False, 1),
    ],
)
def test_cli_distinguishes_import_alias_changes(
    tmp_path: Path, before: str, after: str, call_added: bool, expected: int
) -> None:
    (tmp_path / "client.py").write_text(f"from httpx import {after}\nclient = AsyncClient()\n")
    call = "+client = AsyncClient()\n" if call_added else " client = AsyncClient()\n"
    diff = (
        "--- a/client.py\n+++ b/client.py\n"
        f"@@ -1,{1 if call_added else 2} +1,2 @@\n"
        f"-from httpx import {before}\n+from httpx import {after}\n{call}"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=diff,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == expected, result.stderr


@pytest.mark.parametrize("previous_module,expected", [("httpx2", 1), ("httpx", 0)])
@pytest.mark.parametrize("changed_first", [False, True])
def test_cli_distinguishes_repeated_branch_imports(
    tmp_path: Path, previous_module: str, expected: int, changed_first: bool
) -> None:
    source = (
        "def client(sdk):\n"
        "    if sdk:\n"
        "        from httpx import AsyncClient\n"
        "        return AsyncClient()\n"
        "    else:\n"
        "        from httpx import AsyncClient\n"
        "        return AsyncClient()\n"
    )
    (tmp_path / "client.py").write_text(source)
    line = 3 if changed_first else 6
    diff = (
        f"--- a/client.py\n+++ b/client.py\n@@ -{line} +{line} @@\n"
        f"-        from {previous_module} import AsyncClient\n"
        "+        from httpx import AsyncClient\n"
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=diff,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == expected, result.stderr
    assert (f"client.py:{line}:" in result.stderr) == bool(expected)


@pytest.mark.parametrize("sdk_first", [False, True])
def test_cli_allows_sdk_only_branch_import(tmp_path: Path, sdk_first: bool) -> None:
    owned = "        from httpx import AsyncClient\n        return AsyncClient()\n"
    sdk = "        from httpx import AsyncClient\n        return sdk.use(AsyncClient)\n"
    prefix = "def client(owned, sdk):\n    if owned:\n"
    before = prefix + ("        pass\n    else:\n" + owned if sdk_first else owned)
    after = prefix + (sdk if sdk_first else owned) + "    else:\n" + (owned if sdk_first else sdk)
    (tmp_path / "client.py").write_text(after)
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/client.py",
            tofile="b/client.py",
        )
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=diff,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("late_import", [False, True])
def test_cli_checks_rebound_imports(tmp_path: Path, late_import: bool) -> None:
    before = (
        "import httpx2 as h\ndef create():\n    return h.Client()\ncreate()\n"
        if late_import
        else "def client(owned, sdk):\n    if owned:\n"
        "        from httpx2 import AsyncClient\n        return AsyncClient()\n"
        "    else:\n        from httpx import AsyncClient\n        return sdk.use(AsyncClient)\n"
    )
    after = (
        before.replace("create()\n", "import httpx as h\ncreate()\n")
        if late_import
        else before.replace("from httpx2 import", "from TEMP import")
        .replace("from httpx import", "from httpx2 import")
        .replace("from TEMP import", "from httpx import")
    )
    (tmp_path / "client.py").write_text(after)
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="a/client.py",
            tofile="b/client.py",
        )
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=diff,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 1, result.stderr
