Start Robin's automatic reviewer assignment for a pull request's open review request — the same process that runs once a request has waited too long without a reviewer.
Use it when someone asks you to auto-assign or pick a reviewer for a pull request that has a review card or is being watched from a review channel post.
Pass the pull request URL.

Robin only picks people who posted within the last 30 days in the channel where the review request was posted, including replies in its latest 100 top-level messages.
Within that pool it ranks CODEOWNERS, who recently changed the files, and how many reviews each person already has, excluding people who opted out and choosing only people inside their work hours; when nobody is, it waits for the first person's work day to start.
When nobody qualifies at all, a separate run may choose another recent poster under the same restrictions.
If nobody qualifies or channel history cannot be read, the request stays unassigned; it never falls back to unrelated people.
Whoever is picked is tagged in the review's Slack thread, has their review requested on GitHub, and is messaged directly.
Do not call `assign_human_reviewer` yourself afterwards.
