"""Only the Open SWE backend calls this deployment, with its LangSmith service key.

Its threads are its own. Threads a channel starts belong to that channel's
conversation: the backend reads them as LangSmith Studio does, and acts in them
through the trusted-backend ingress.

Before every run a channel starts, MDA asks the backend's ``/mda/resolve-user``
who its verified sender is in Open SWE, signed with ``MDA_SERVICE_SECRET``: a
Slack, GitHub or Linear account its user has linked runs as that user, and
anyone else as the channel's own principal.
"""

from managed_deepagents import auth, define_identity
from rt.intake import backend_endpoint

identity = define_identity(
    auth=auth.langsmith_api_key(), resolve_user=backend_endpoint("/mda/resolve-user")
)
