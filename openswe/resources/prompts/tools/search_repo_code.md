Search code in the PR's repository for a keyword, symbol, or phrase.

Backed by GitHub code search, which indexes the repository's default branch
(not arbitrary refs). Use it to locate where a symbol is defined or used,
then ``read_repo_file`` for the surrounding context. For matches within the
changed lines, search the virtual file ``/pr/diff.patch`` instead.

Args:
    query: Search terms. Repo scoping is added automatically.
    max_results: Max matches to return (capped at 50).

Returns:
    ``{success, total_count, results}`` where each result is
    ``{path, fragments}``; ``{success: False, error}`` on failure.
