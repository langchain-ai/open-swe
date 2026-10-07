import subprocess
import sys

import pytest

from openswe.utils.html_artifact import (
    artifact_skeleton,
    sandbox_wrap_command,
    wrap_html_artifact,
)

# sandbox_wrap_command emits a POSIX pipeline (printf | base64 -d | python3 -) for
# the Linux sandbox to run. subprocess.run(shell=True) sends it through cmd.exe on
# Windows, which has neither base64 nor python3 and does not treat ' as a quote.
# Deliberately keyed on the platform rather than on tool availability, so a POSIX
# CI box missing python3 fails loudly instead of quietly skipping.
_requires_posix_shell = pytest.mark.skipif(
    sys.platform == "win32",
    reason="sandbox_wrap_command is a POSIX shell pipeline",
)


@pytest.mark.parametrize(
    "content",
    [
        '<!doctype html><html lang="en"><body>x</body></html>',
        '<html\nlang="en">\n<body>x</body>\n</html>',
        "<html>\n<body>x</body>\n</html>",
        "<h1>fragment</h1>",
        "<htmlish>not a document</htmlish>",
        "<p>mentions &lt;html&gt; in prose</p>",
        "<h1>caf\u00e9 \u2014 na\u00efve</h1>",
    ],
)
@_requires_posix_shell
def test_sandbox_command_matches_in_process_wrapping(content: str, tmp_path) -> None:
    source = tmp_path / "artifact.html"
    destination = tmp_path / "snapshot.html"
    source.write_text(content, encoding="utf-8")

    command = sandbox_wrap_command(
        str(source), str(destination), limit=1_000_001, title="Quarterly chart"
    )
    result = subprocess.run(command, shell=True, capture_output=True, text=True, check=True)

    expected = wrap_html_artifact(content, title="Quarterly chart")
    assert destination.read_text(encoding="utf-8") == expected
    assert int(result.stdout.strip()) == len(expected.encode())


@_requires_posix_shell
def test_sandbox_command_truncates_at_the_limit(tmp_path) -> None:
    source = tmp_path / "artifact.html"
    destination = tmp_path / "snapshot.html"
    source.write_text("<h1>" + "x" * 500 + "</h1>", encoding="utf-8")

    subprocess.run(
        sandbox_wrap_command(str(source), str(destination), limit=32),
        shell=True,
        capture_output=True,
        check=True,
    )

    assert "<h1>" + "x" * 28 in destination.read_text(encoding="utf-8")


def test_skeleton_escapes_the_title() -> None:
    prefix, suffix = artifact_skeleton('Plan <b>"one"</b>')

    assert "<title>Plan &lt;b&gt;&quot;one&quot;&lt;/b&gt;</title>" in prefix
    assert suffix.strip().endswith("</html>")
