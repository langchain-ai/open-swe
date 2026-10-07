from openswe.slack.markdown import markdown_to_mrkdwn


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


def test_markdown_to_mrkdwn_separates_unterminated_fence_content() -> None:
    assert markdown_to_mrkdwn("```\ncode") == "```\ncode\n```"


def test_markdown_to_mrkdwn_keeps_slack_mentions_and_escapes_other_brackets() -> None:
    assert (
        markdown_to_mrkdwn("hi <@U06KD8BFY95> in <#C123|general> <!here>, a < b")
        == "hi <@U06KD8BFY95> in <#C123|general> <!here>, a &lt; b"
    )
