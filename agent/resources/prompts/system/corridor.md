---

<corridor>

# Corridor Security Analysis
Use the `analyzePlan` tool from Corridor's MCP server before substantial code changes that touch security-sensitive areas, such as authentication, authorization, secrets, untrusted input, data access, or externally exposed APIs.

Corridor is offered from configuration, so its server can still be unreachable. If loading or calling `analyzePlan` reports it unavailable, say so once and carry on with the task — do not retry it and do not treat it as a blocker.

</corridor>
