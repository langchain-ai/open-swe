import pytest
from pydantic import ValidationError

from agent.investigations.models import InvestigationPolicy


@pytest.mark.parametrize("prefix", ["", "#", "inc *", "*"])
def test_rejects_prefixes_that_could_enroll_unrelated_channels(prefix):
    with pytest.raises(ValidationError):
        InvestigationPolicy(channel_prefix=prefix)


def test_normalizes_prefix():
    assert InvestigationPolicy(channel_prefix="#Inc-").channel_prefix == "inc-"


def test_model_and_execution_budget_are_bounded():
    with pytest.raises(ValidationError):
        InvestigationPolicy(max_model_calls=0)
    with pytest.raises(ValidationError):
        InvestigationPolicy(max_pass_seconds=3600)
