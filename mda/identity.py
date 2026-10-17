"""Only the Open SWE backend calls this deployment, with its LangSmith service key.

Its threads are its own. Threads of Linear sessions belong to the Linear channel:
the backend reads those as LangSmith Studio does (``x-auth-scheme: langsmith``),
which MDA allows for reads only, and starts no runs on them.
"""

from managed_deepagents import auth, define_identity

identity = define_identity(auth=auth.langsmith_api_key())
