List surfaced threads by locator, participant, admin mode, status, source, or text.
Pass a canonical GitHub pull request URL as `query` to find accessible threads linked to that PR, including the original implementation conversation.
By default, results are limited to threads you own or participate in; pass `all_users=True` to search other authors' accessible threads as well.
Follow `has_more` with `offset` to page through matches.
`summary_only=True` returns compact metadata and the last available assistant outcome as `final_message`, capped at 500 characters (null if unavailable); this defaults on for `scope="automation"`.
Compact history supplies prior outcomes inline without a `get_thread` call per run.
Use smaller `limit` pages if long outcomes exceed the inline result limit.
Pass `summary_only=False` for full list metadata, and use `get_thread` for deeper inspection or truncated retry details.
In an automation run, results cover every public thread.
Results obey the triggering user's access and privacy restrictions; an empty result does not prove no original thread exists.
