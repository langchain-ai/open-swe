from copy import deepcopy

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI


def test_stateless_responses_replay_preserves_tool_history_without_mutation() -> None:
    messages = [
        HumanMessage("test the todo middleware"),
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_reasoning",
                    "summary": [],
                    "encrypted_content": "encrypted-reasoning",
                },
                {
                    "type": "function_call",
                    "id": "fc_execute",
                    "call_id": "call_execute",
                    "name": "execute",
                    "arguments": '{"command":"pwd"}',
                },
            ],
            tool_calls=[
                {
                    "name": "execute",
                    "args": {"command": "pwd"},
                    "id": "call_execute",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content="/workspace/open-swe",
            tool_call_id="call_execute",
            name="execute",
        ),
        HumanMessage("continue"),
    ]
    original_messages = deepcopy(messages)
    model = ChatOpenAI(
        model="gpt-5.6-sol",
        api_key="test",
        use_responses_api=True,
        store=False,
        include=["reasoning.encrypted_content"],
        output_version="responses/v1",
    )

    first_payload = model._get_request_payload(messages)
    second_payload = model._get_request_payload(messages)

    expected_call = {
        "type": "function_call",
        "id": "fc_execute",
        "call_id": "call_execute",
        "name": "execute",
        "arguments": '{"command":"pwd"}',
    }
    expected_output = {
        "type": "function_call_output",
        "output": "/workspace/open-swe",
        "call_id": "call_execute",
    }
    assert expected_call in first_payload["input"]
    assert expected_output in first_payload["input"]
    assert second_payload["input"] == first_payload["input"]
    assert messages == original_messages
