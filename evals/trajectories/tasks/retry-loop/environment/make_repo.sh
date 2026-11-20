#!/bin/sh
# Builds /app's history: the merged PRs name who asked for each change, which is how a
# run that stops retrying the search finds whose threads to list.
set -eu
cd /app
git init -q -b main
commit() {
    printf '%s\n' "$2" >> "$1"
    git add -A
    shift 2
    git -c user.name=Robin -c user.email=robin@widgets.dev commit -q "$@"
}
commit src/client.py 'DEFAULT_RETRIES = 4' -m 'feat(client): retry failed requests (#4702)'
commit tests/test_client.py 'def test_retry_backoff(): pass' \
    -m 'fix(tests): stop test_retry_backoff flaking (#4790)' \
    -m 'Co-authored-by: Alice Ng <alice@widgets.dev>'
commit src/client.py 'RETRY_BUDGET_PER_CLIENT = 10' \
    -m 'feat(client): cap the retry budget per client (#4812)' \
    -m 'Co-authored-by: Alice Ng <alice@widgets.dev>'
commit docs/retries.md 'Each client gets 10 retries per minute.' \
    -m 'docs: describe the retry budget (#4821)' \
    -m 'Co-authored-by: Bartek Kowalski <bartek@widgets.dev>'
