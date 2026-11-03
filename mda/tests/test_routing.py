from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from rt.input_messages import human_input
from rt.routing import Route, RouteClassifier


@dataclass
class _Answer:
    choice: str
    confidence: float


@dataclass
class _Response:
    choices: dict[str, _Answer]


class _Classifier:
    """The gateway's classifier, answering every question with ``answer``."""

    answer: _Answer | Exception = _Answer("fast", 0.9)
    asked: list[dict[str, object]] = []

    def __init__(self, *, model: str, api_key: str, base_url: str, async_client: object) -> None:
        self.api_key = api_key
        self.base_url = base_url

    async def ainvoke(self, request: dict[str, object], config: dict[str, object]) -> _Response:
        _Classifier.asked.append({**request, "config": config, "base_url": self.base_url})
        if isinstance(_Classifier.answer, Exception):
            raise _Classifier.answer
        return _Response({"route": _Classifier.answer})


@pytest.fixture(autouse=True)
def _gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "gateway-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://gateway.invalid/")
    monkeypatch.setattr("rt.routing.TypeSafeClassifier", _Classifier)
    monkeypatch.setattr(_Classifier, "answer", _Answer("fast", 0.9))
    monkeypatch.setattr(_Classifier, "asked", [])


def test_the_task_is_the_persons_own_request_not_the_context_after_it() -> None:
    request = human_input(
        "Rename the flag", {"sender_id": "slack:U1", "surface": "slack", "kind": "human"}
    )
    authored = request["content"]
    assert isinstance(authored, str)
    messages = [
        HumanMessage(content="An older request"),
        HumanMessage(content=authored),
        HumanMessage(content='<dynamic-context kind="person" id="u1">Alice</dynamic-context>'),
        AIMessage(content="On it"),
    ]

    assert RouteClassifier.latest_task(messages) == "Rename the flag"
    assert RouteClassifier.latest_task([HumanMessage(content="Plain text")]) == "Plain text"


async def test_a_confident_choice_is_kept_for_the_thread() -> None:
    route = await RouteClassifier().classify([HumanMessage(content="Rename the flag")])

    assert route == Route("fast")
    [asked] = _Classifier.asked
    assert asked["state"] == "Rename the flag"
    assert asked["config"] == {"tags": ["nostream"]}
    assert asked["base_url"] == "https://gateway.invalid"
    questions = asked["questions"]
    assert isinstance(questions, dict)
    assert sorted(questions["route"].criteria) == ["balanced", "fast", "performance"]


@pytest.mark.parametrize(
    "answer",
    [
        _Answer("fast", 0.4),
        _Answer("fast", float("nan")),
        _Answer("cheapest", 0.9),
        RuntimeError("gateway down"),
    ],
    ids=["unsure", "nan", "unknown-route", "failed"],
)
async def test_a_route_the_classifier_cannot_give_is_the_default_for_this_run_alone(
    monkeypatch: pytest.MonkeyPatch, answer: _Answer | Exception
) -> None:
    monkeypatch.setattr(_Classifier, "answer", answer)

    route = await RouteClassifier().classify([HumanMessage(content="Fix it")])

    assert route == Route("default", persist=False)


async def test_without_a_gateway_key_the_classifier_is_not_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_GATEWAY_API_KEY")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)

    route = await RouteClassifier().classify([HumanMessage(content="Fix it")])

    assert route == Route("default", persist=False)
    assert _Classifier.asked == []
