Offer the person cards to connect services in this workspace's managed tools (a LangSmith Managed Tools gateway an admin picked).

Managed tools are integration tools named `mcp_lmt_*`, and they run on the person's own accounts, so each service loads only once the person has connected it.
When the person asks you to use a service (Linear, Notion, Pylon and the like, or asks to connect one) and neither your tools nor `search_tools` show `mcp_lmt_*` tools for it, call this with that service's name in `services` instead of saying you lack access.
Leave `services` empty only when the person asks to connect everything.
Do not call it in a thread you are not running for its private owner.

The result lists the services the card offers.
Some gateways offer no tools until every service in them is connected (`all_required`); the card then offers all of them, whatever you named.
On the web it shows an inline card; in Slack it posts a card with one button per service as your final reply, so do not send a duplicate reply after a successful Slack call.
Success means the card was offered, not that anything is connected.
When `continues_automatically` is true, a new run continues this thread once the offered services are connected; stop and wait for it instead of retrying.
Otherwise some service needs an API key set in LangSmith, and the person asks you to continue when it is.

If the result says the person must connect LangSmith first, the card offers that; tell them to connect it and then ask again.
If it says nothing is waiting to be connected, the services are either connected already (their tools load on the person's next message) or not in this gateway; in Slack the tool has already told them so.
