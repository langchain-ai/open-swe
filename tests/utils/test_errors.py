import httpx2
import openai

from agent.utils.errors import classify_exception


def test_classifies_openai_content_policy_refusal() -> None:
    exc = openai.APIError(
        "This content was flagged for possible cybersecurity risk. "
        "If this seems wrong, try rephrasing your request.",
        request=httpx2.Request("POST", "https://api.openai.com/v1/responses"),
        body=None,
    )

    assert classify_exception(exc) == "provider_refused"


def test_classifies_content_policy_error_body_code() -> None:
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx2.Response(
        400, request=request, json={"error": {"type": "content_policy_violation"}}
    )
    exc = openai.APIStatusError("Bad request", response=response, body=response.json())

    assert classify_exception(exc) == "provider_refused"
