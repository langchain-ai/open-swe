from agent.logging_redaction import install_webhook_token_redaction

# Graph workers import agent.graphs.* entrypoints without the HTTP app module,
# so completion-webhook token redaction must also install from this package.
install_webhook_token_redaction()

__all__: list[str] = []
