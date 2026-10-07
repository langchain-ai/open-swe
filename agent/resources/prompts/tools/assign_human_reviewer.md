Assign a reviewer to a pull request's open review request. Pass the pull request URL, the reviewer's GitHub login, and `reason`: one short sentence on why them, such as owning the changed code, having reviewed it recently, or a person asking for them by name. It shows them on the review card, requests their review on GitHub, and messages them directly with Accept, Decline and Snooze. Until they accept, anyone else may still sign up; if they do not accept in time, Open SWE asks someone else. Nothing is posted in the Slack thread.

A pull request usually has one reviewer first, from the code owners of most of its changed files. More reviewers join only for code owner areas nobody on it owns yet, and Open SWE adds those only after the first reviewer approves.

When a person names who should review ("assign @alice", "can Bob review this?"), pass their GitHub login with `named_by_person=true`: that works from any thread, withdraws Open SWE's pending picks for the code they own, and otherwise adds them alongside the current reviewers. Resolve a Slack mention to its GitHub login from the person's details in context. Leave `named_by_person` false for picks you make yourself, which only the thread woken to pick may make.

The reviewer must be an Open SWE user with write access to the repository, must not be the pull request's author, and must not be someone who already let this pick expire; when someone is refused, pick the next best candidate.

Never link to the card: Slack unfurls that link into a second copy of it.
