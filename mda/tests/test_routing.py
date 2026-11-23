from dataclasses import dataclass

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, convert_to_messages
from rt.input_messages import InputMessageContext, RunMessage, human_input, system_input
from rt.routing import Route, RouteClassifier


@dataclass
class _Answer:
    choice: str
    confidence: float


@dataclass
class _Response:
    choices: dict[str, _Answer]


class _Classifier:
    """The gateway's classifier, answering each question from ``answers``, or every question
    with ``answer``."""

    answer: _Answer | Exception = _Answer("lookup", 0.9)
    answers: dict[str, _Answer] = {}
    asked: list[dict[str, object]] = []

    def __init__(self, *, model: str, api_key: str, base_url: str, async_client: object) -> None:
        self.api_key = api_key
        self.base_url = base_url

    async def ainvoke(self, request: dict[str, object], config: dict[str, object]) -> _Response:
        _Classifier.asked.append({**request, "config": config, "base_url": self.base_url})
        if isinstance(_Classifier.answer, Exception):
            raise _Classifier.answer
        questions = request["questions"]
        assert isinstance(questions, dict)
        return _Response(
            {name: _Classifier.answers.get(name, _Classifier.answer) for name in questions}
        )


@pytest.fixture(autouse=True)
def _gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_GATEWAY_API_KEY", "gateway-key")
    monkeypatch.setenv("LANGSMITH_GATEWAY_BASE_URL", "https://gateway.invalid/")
    monkeypatch.setattr("runtime_api.classifier.TypeSafeClassifier", _Classifier)
    monkeypatch.setattr(_Classifier, "answer", _Answer("lookup", 0.9))
    monkeypatch.setattr(_Classifier, "answers", {})
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


def _message(built: RunMessage) -> BaseMessage:
    """A run input message, as the run's state holds it."""
    [message] = convert_to_messages([dict(built)])
    return message


def _system(text: str, sender: str) -> BaseMessage:
    return _message(system_input(text, {"sender_id": sender, "surface": "web", "kind": "system"}))


def test_a_run_no_person_started_is_classified_by_its_latest_event() -> None:
    # A schedule's run carries only what the schedule sent, which is its task.
    messages = [
        HumanMessage(
            content='<dynamic-context kind="system" id="system:schedule:s1">Daily</dynamic-context>'
        ),
        _system("Earlier event", "system:schedule:s1"),
        _system("Audit the dependencies", "system:schedule:s1"),
    ]

    assert RouteClassifier.latest_task(messages) == "Audit the dependencies"


def test_a_persons_request_wins_over_a_later_event() -> None:
    request = human_input(
        "Rename the flag", {"sender_id": "slack:U1", "surface": "slack", "kind": "human"}
    )
    messages = [
        _message(request),
        _system("CI finished", "system:ci"),
    ]

    assert RouteClassifier.latest_task(messages) == "Rename the flag"


def test_an_attachment_only_turn_does_not_reuse_an_older_request() -> None:
    web: InputMessageContext = {"sender_id": "user:u1", "surface": "web", "kind": "human"}
    attachment: InputMessageContext = {
        **web,
        "data": {"attached_file": [{"sandbox_path": "/tmp/invoice.pdf"}]},
    }
    messages = [
        _message(human_input("Build the settings page", web)),
        AIMessage(content="Done"),
        _message(human_input("", attachment)),
    ]

    assert RouteClassifier.latest_task(messages) == ""


async def test_a_confident_choice_is_kept_for_the_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _Classifier,
        "answers",
        {"investigate": _Answer("no", 0.9), "carve_out": _Answer("specified_design", 0.9)},
    )

    route = await RouteClassifier().classify([HumanMessage(content="Rename the flag")])

    # A specified change runs on Balanced, and the trace records what Jev said about it.
    assert route == Route("balanced")
    assert route.trace_metadata == {
        "jev_task_type": "lookup",
        "jev_investigate": "no",
        "jev_carve_out": "specified_design",
    }
    [asked] = _Classifier.asked
    assert asked["state"] == "Rename the flag"
    assert asked["config"] == {"tags": ["nostream"]}
    assert asked["base_url"] == "https://gateway.invalid"
    questions = asked["questions"]
    assert isinstance(questions, dict)
    assert sorted(questions) == ["carve_out", "investigate", "task_type"]


@pytest.mark.parametrize(
    "answer",
    [
        _Answer("design", 0.4),
        _Answer("design", float("nan")),
        _Answer("cheapest", 0.9),
        RuntimeError("gateway down"),
    ],
    ids=["unsure", "nan", "unknown-task-type", "failed"],
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


@pytest.mark.parametrize(
    ("investigate", "carve_out", "expected"),
    [
        (_Answer("yes", 0.9), _Answer("none", 0.9), Route("performance")),
        (_Answer("yes", 0.9), _Answer("lookup_status", 0.9), Route("performance")),
        (_Answer("yes", 0.9), _Answer("specified_design", 0.9), Route("balanced")),
        (_Answer("yes", 0.9), _Answer("debug_known", 0.9), Route("balanced")),
        (_Answer("no", 0.9), _Answer("none", 0.9), Route("balanced")),
        (_Answer("yes", 0.4), _Answer("none", 0.9), Route("performance", persist=False)),
    ],
    ids=["open-ended", "open-lookup", "specified", "known-fix", "known-answer", "unsure"],
)
async def test_only_open_ended_investigation_runs_on_performance(
    monkeypatch: pytest.MonkeyPatch, investigate: _Answer, carve_out: _Answer, expected: Route
) -> None:
    monkeypatch.setattr(
        _Classifier, "answers", {"investigate": investigate, "carve_out": carve_out}
    )

    route = await RouteClassifier().classify([HumanMessage(content="Why are the evals slower?")])

    assert route == expected


async def test_a_long_request_is_read_from_its_start_and_its_end() -> None:
    question = "Why is the cache cold?"
    history = "channel history " * 1_000

    await RouteClassifier().classify([HumanMessage(content=f"{question}\n{history}")])

    [asked] = _Classifier.asked
    state = asked["state"]
    assert isinstance(state, str)
    # A Slack question comes before its channel history; the classifier still reads it.
    assert state.startswith(question)
    assert state.endswith(history[-100:])
    assert len(state) < len(question) + len(history)
