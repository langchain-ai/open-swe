"""Who may call this deployment."""

from managed_deepagents import auth, define_identity

# LangSmith workspace API keys authenticate callers through `x-api-key` while
# retaining MDA's thread and store authorization hooks.
#
# Managed identity gives every caller private threads and downstream
# credentials. Durable memory is not an identity axis — declare it in memory.py.
identity = define_identity(auth=auth.langsmith_api_key())
