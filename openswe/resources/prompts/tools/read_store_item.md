Read any LangGraph Store item by its exact namespace (a list of strings) and key, without modifying it. Returns the stored value and whether the item exists; missing items are not errors.

Requires a currently authorized workspace admin on a private web thread, authenticated Slack DM, or admin CLI MCP session. Not available in shared threads, including single-writer threads. Namespaces are unrestricted; treat returned records as sensitive, never decrypt stored credentials, and never publish secrets or credentials.
