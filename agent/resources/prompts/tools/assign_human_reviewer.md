Assign a reviewer to a pull request's open review request when nobody signed up for it. Pass the pull request URL, the reviewer's GitHub login, and `reason`: one short sentence on why them, such as owning the changed code or having reviewed it recently. It adds them to the card, requests their review on GitHub, tags them in Slack, and messages them directly.

The reviewer must be an Open SWE user with write access to the repository and must not be the pull request's author; when someone is refused, pick the next best candidate.
