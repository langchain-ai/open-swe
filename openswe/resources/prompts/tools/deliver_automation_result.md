Send this automation run's result as a Slack DM to the person who created the automation.
There is no recipient argument: the creator is the only person this tool can reach, and it reaches them only while they are still allowed to use Open SWE.
Use it when the automation's instructions ask for results to be sent to its creator, to "me", or to their DMs.

Call it once per run, with the complete result in `message`.
Write `message` in standard Markdown: **bold**, _italic_, [link text](url), and <@USER_ID> mentions, and keep it under 40,000 characters.
A footer naming the automation and linking this run is added for you.

On failure, report the error as the run's outcome.
For Slack transport errors or `post_failed`, delivery is uncertain: do not retry automatically, because doing so could send the DM twice.
