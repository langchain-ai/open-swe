Offer the person cards to connect the services in this workspace's managed tools (a LangSmith Managed Tools gateway an admin picked).

Managed tools are integration tools named `mcp_lmt_*`. LangSmith offers them only once the person has connected every service in the gateway, so none load until then. Call this when the person asks for something those services would cover (or asks to connect them) and no `mcp_lmt_*` tools are listed. Do not call it in a thread you are not running for its private owner.

The result lists the gateway's services the person still has to connect. On the web it shows an inline card; in Slack it posts a card with one button per service as your final reply, so do not send a duplicate reply after a successful Slack call. Success means the card was offered, not that anything is connected. When `continues_automatically` is true, a new run continues this thread once every service is connected; stop and wait for it instead of retrying. Otherwise some service needs an API key set in LangSmith, and the person asks you to continue when it is.

If the result says the person must connect LangSmith first, the card offers that; tell them to connect it and then ask again. If it says the tools are already connected, they load on the person's next message; in Slack the tool has already told them so.
