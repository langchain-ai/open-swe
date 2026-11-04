# Structured JSON tool results in code mode

**Upstream as-is.**

`langchain_quickjs/_format.py` parses successful ToolMessage string content containing a JSON object or array at the PTC conversion boundary.
Open SWE's backend serializes structured tool results into strings, so eval otherwise cannot read their fields as the API reference promises.
Plain strings, JSON scalars and error ToolMessages retain their existing representation, including inside Command updates, and direct tool calls are unaffected.
Drop this change once upstream's PTC coercion supports serialized structured results while preserving error status during envelope unwrapping.
