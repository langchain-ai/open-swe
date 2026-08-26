from agent.utils.html_artifact import (
    DEFAULT_TITLE,
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


def test_skeleton_escapes_the_title() -> None:
    prefix, suffix = artifact_skeleton('Plan <b>"one"</b>')

    assert "<title>Plan &lt;b&gt;&quot;one&quot;&lt;/b&gt;</title>" in prefix
    assert suffix.strip().endswith("</html>")
