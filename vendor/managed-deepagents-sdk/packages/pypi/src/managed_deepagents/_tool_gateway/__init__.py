"""Everything MDA knows about LangSmith Tool Gateway.

- ``auth``: the contract. A server with ``auth: "langsmith"`` is a normal MCP
  server called with the deployment's LangSmith key and workspace, and whose
  credentials Tool Gateway uses.
- ``cards``: shows Tool Gateway's MCP elicitations with the connect and
  approval cards Studio and Slack render. Removable once they render MCP
  elicitation themselves.
- ``connect_first``: temporary. A single server answers ``tools/list`` with
  428 until the person connects; this asks before the run.
"""
