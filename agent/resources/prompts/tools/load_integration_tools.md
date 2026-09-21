Load connected integration tool schemas before using them. Pass exact tool names listed below, then call the loaded tools normally on your next turn.

## Bound the result

When a loaded integration tool exposes a result-size or paging parameter, such as `max_chars_per_page`, `limit`, `page_size`, or `offset`, set it on the first call rather than fetching an unbounded page.
