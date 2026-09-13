This is an internal stop-summary turn triggered by a Slack :x: reaction, not a new task request. $observed_state

Do not resume or continue the prior task. Do not modify files, run mutating commands, commit, push, open or update a pull request, or take any other implementation action. Inspect only the existing conversation and current sandbox state with read-only tools as needed.

Your first and only user-facing action must be one concise `slack_thread_reply` that factually summarizes what was completed, what was in progress when interrupted, and what remains. If no active run existed, say so. Do not post an acknowledgement before the summary. End immediately after posting it.
