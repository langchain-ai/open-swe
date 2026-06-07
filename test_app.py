import json

import pytest

from app import app
from src.greeting_handler import reset_state


@pytest.fixture
def client():
    reset_state()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_basic_hello(client):
    response = client.post("/greet", json={"message": "hello"})
    data = json.loads(response.data)
    assert response.status_code == 200
    assert data["status"] == "success"
    assert "Hello!" in data["response"]


def test_case_insensitive(client):
    reset_state()
    for msg in ["HELLO", "Hello", "hELLO"]:
        response = client.post("/greet", json={"message": msg})
        data = json.loads(response.data)
        assert data["status"] == "success"


def test_whitespace(client):
    reset_state()
    response = client.post("/greet", json={"message": "  hello  "})
    data = json.loads(response.data)
    assert data["status"] == "success"


def test_empty_input(client):
    response = client.post("/greet", json={"message": ""})
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data


def test_non_string_input(client):
    for invalid in [123, None, True, []]:
        response = client.post("/greet", json={"message": invalid})
        assert response.status_code == 400


def test_missing_key(client):
    response = client.post("/greet", json={})
    assert response.status_code == 400
    data = json.loads(response.data)
    assert "error" in data


def test_partial_match(client):
    reset_state()
    response = client.post("/greet", json={"message": "hello world"})
    data = json.loads(response.data)
    assert data["status"] == "unrecognized"
