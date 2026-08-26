import subprocess

import pytest

from agent.utils.html_artifact import (
    DEFAULT_TITLE,
    FULL_DOCUMENT_GREP,
    artifact_skeleton,
    is_full_document,
    wrap_html_artifact,
)


def test_full_document_is_returned_untouched() -> None:
    document = '<!doctype html><html lang="en"><head></head><body>hi</body></html>'

    assert is_full_document(document)
    assert wrap_html_artifact(document, title="Ignored") == document


def test_fragment_is_wrapped_with_the_skeleton_and_reset() -> None:
    wrapped = wrap_html_artifact("<h1>Rollout</h1>", title="Rollout plan")

    assert wrapped.startswith("<!doctype html>\n<html>\n<head>\n")
    assert "<title>Rollout plan</title>" in wrapped
    assert "box-sizing:border-box" in wrapped
    assert "<body>\n<h1>Rollout</h1>\n</body>" in wrapped


def test_fragment_title_is_lifted_into_the_head() -> None:
    wrapped = wrap_html_artifact("<title>Real name</title>\n<h1>Body</h1>", title="Fallback")

    assert wrapped.count("<title>") == 1
    assert "<title>Real name</title>" in wrapped
    assert "<body>\n<h1>Body</h1>\n</body>" in wrapped


def test_scripts_and_forms_survive_wrapping() -> None:
    wrapped = wrap_html_artifact("<canvas id=c></canvas><script>draw()</script>")

    assert "<script>draw()</script>" in wrapped
    assert f"<title>{DEFAULT_TITLE}</title>" in wrapped


@pytest.mark.parametrize(
    "content",
    [
        '<!doctype html><html lang="en"><body>x</body></html>',
        '<html\nlang="en">\n<body>x</body>\n</html>',
        "<html>\n<body>x</body>\n</html>",
        "<h1>fragment</h1>",
        "<htmlish>not a document</htmlish>",
        "<p>mentions &lt;html&gt; in prose</p>",
    ],
)
def test_grep_pattern_agrees_with_the_python_detector(content: str, tmp_path) -> None:
    path = tmp_path / "artifact.html"
    path.write_text(content)

    found = subprocess.run(
        ["grep", "-qiE", FULL_DOCUMENT_GREP, "--", str(path)],
        check=False,
    )

    assert (found.returncode == 0) is is_full_document(content)


def test_multiline_html_tag_is_not_double_wrapped() -> None:
    document = '<html\nlang="en">\n<head></head>\n<body>x</body>\n</html>'

    assert wrap_html_artifact(document) == document


def test_skeleton_escapes_the_title() -> None:
    prefix, suffix = artifact_skeleton('Plan <b>"one"</b>')

    assert "<title>Plan &lt;b&gt;&quot;one&quot;&lt;/b&gt;</title>" in prefix
    assert suffix.strip().endswith("</html>")
