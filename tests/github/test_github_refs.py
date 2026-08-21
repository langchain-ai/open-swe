from agent.utils.github_refs import parse_github_pr_url


def test_parse_github_pr_url_raw_url() -> None:
    pr_ref = parse_github_pr_url("https://github.com/langchain-ai/open-swe/pull/1244")

    assert pr_ref is not None
    assert pr_ref.owner == "langchain-ai"
    assert pr_ref.repo == "open-swe"
    assert pr_ref.number == 1244
    assert pr_ref.url == "https://github.com/langchain-ai/open-swe/pull/1244"


def test_parse_github_pr_url_slack_formatted_link() -> None:
    pr_ref = parse_github_pr_url("<https://github.com/langchain-ai/open-swe/pull/1244|PR>")

    assert pr_ref is not None
    assert pr_ref.owner == "langchain-ai"
    assert pr_ref.repo == "open-swe"
    assert pr_ref.number == 1244
