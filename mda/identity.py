"""Only the Open SWE backend calls this deployment, with its LangSmith service key.

It is the deployment's trusted service (``MDA_SERVICE_SECRET``), so it also serves
and continues the threads of Linear sessions, which belong to the Linear channel.
"""

from managed_deepagents import auth, define_identity

identity = define_identity(auth=auth.langsmith_api_key(), trusted_service=True)
