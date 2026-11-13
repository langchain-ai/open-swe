Offer the person cards to connect their own accounts to LangSmith Managed Tools services (Linear, Notion, Google, Datadog and the like).

Managed tools are named `mcp_lmt_*`, and in a private thread they run on its owner's own accounts.
Every service in Robin's personal kit lists its tools whether or not the person connected it; calling one they have not connected fails and tells you to call this.
When that happens, or the person asks to connect a service, call this with that service's name in `services` instead of saying you lack access.
Name the services the person asked for; leave `services` empty only when they ask to connect everything.
Do not call it in a thread you are not running for its private owner.

The result lists the services the card offers.
On the web it shows an inline card; in Slack it posts a card with one button per service as your final reply, so do not send a duplicate reply after a successful Slack call.
Success means the card was offered, not that anything is connected.
When `continues_automatically` is true, a new run continues this thread once the offered services are connected; stop and wait for it instead of retrying.
Otherwise some service needs an API key, which its button sets up, and the person asks you to continue when it is.

If the result says the person must sign in with LangSmith again, the card offers that; tell them to sign in and then ask again.
If it says nothing is waiting to be connected, the services are either connected already (their tools load on the person's next message) or not LangSmith services; in Slack the tool has already told them so.
