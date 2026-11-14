# Decode structured tool results in code mode

**Upstream as-is.**

`langchain_quickjs/_format.py` decodes JSON object and array strings in `ToolMessage` results before marshalling them into JavaScript.
List and `Command` envelopes retain the selected message's tool identity so file and shell tools keep returning text, including valid JSON text.
Bare strings, content blocks and malformed JSON remain unchanged, and message selection follows the existing rules.
Open SWE needs this because its backend operations serialize structured results into tool messages, while code mode promises directly usable objects and arrays.
The focused tests exercise coercion and a QuickJS `tools.listThreads` call that reads nested items without parsing.
Drop this change once upstream decodes structured tool messages while preserving text tools and envelope selection.
