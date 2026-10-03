Assign a reviewer to a pull request's open review request when nobody signed up for it. Pass the pull request URL, the reviewer's GitHub login, and `reason`: one short sentence on why them, such as owning the changed code or having reviewed it recently. It requests their review on GitHub, tags them in Slack, and messages them directly with an Accept button. Until they accept, anyone else may still sign up; if they do not accept in time, Open SWE asks someone else.

The reviewer must be an Open SWE user with write access to the repository, must not be the pull request's author, and must not be someone who already let this pick expire; when someone is refused, pick the next best candidate.

Never link to the card: Slack unfurls that link into a second copy of it.
