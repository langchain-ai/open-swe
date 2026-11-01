#!/bin/sh
# Posts the hook event on stdin to Open SWE. The endpoint answers with an empty
# body, and a failed post must never affect the session, so this always exits 0
# with no output.
timeout="${1:-5}"
curl --silent --show-error --max-time "$timeout" --request POST \
  --header "Authorization: Bearer ${CLAUDE_PLUGIN_OPTION_TOKEN}" \
  --header "Content-Type: application/json" \
  --data-binary @- \
  "${CLAUDE_PLUGIN_OPTION_SERVER_URL%/}/dashboard/api/claude-code/hooks" >/dev/null 2>&1
exit 0
