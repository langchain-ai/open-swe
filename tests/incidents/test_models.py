import pytest
from pydantic import ValidationError

from openswe.incidents.models import IncidentPolicy


@pytest.mark.parametrize("prefix", ["", "#", "inc *", "*"])
def test_rejects_prefixes_that_could_enroll_unrelated_channels(prefix):
    with pytest.raises(ValidationError):
        IncidentPolicy(channel_prefix=prefix)
