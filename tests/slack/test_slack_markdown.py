from agent.slack.markdown import markdown_to_mrkdwn


def test_markdown_to_mrkdwn_common_constructs() -> None:
    markdown = """# Heading

- **bold** and *italic* and ~~gone~~
- [link](https://example.com?a=1&b=2)

> quoted

```python
if a < b and c > d:
    print("*literal*")
```

Use `x < y & z`.
"""

    assert (
        markdown_to_mrkdwn(markdown)
        == """*Heading*
• *bold* and _italic_ and ~gone~
• <https://example.com?a=1&amp;b=2|link>
> quoted
```\nif a &lt; b and c &gt; d:
    print("*literal*")
```
Use `x &lt; y &amp; z`."""
    )


def test_markdown_to_mrkdwn_preserves_html() -> None:
    assert markdown_to_mrkdwn("<div>important content</div>") == (
        "&lt;div&gt;important content&lt;/div&gt;"
    )


def test_markdown_to_mrkdwn_quotes_heading_once() -> None:
    assert markdown_to_mrkdwn("> # Heading") == "> *Heading*"


def test_markdown_to_mrkdwn_numbers_items_not_paragraphs() -> None:
    markdown = "1. first paragraph\n\n   same item\n2. second item"
    assert markdown_to_mrkdwn(markdown) == "1. first paragraph\nsame item\n2. second item"


def test_markdown_to_mrkdwn_separates_unterminated_fence_content() -> None:
    assert markdown_to_mrkdwn("```\ncode") == "```\ncode\n```"
