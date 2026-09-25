This checkout is the preview deployment being assembled: `main` plus every labelled pull request that merged cleanly. The pull requests listed below (`<sha> <merge message>`) conflicted with it. Get as many of them as you can into the preview on top of `HEAD`, each as its own merge commit, with the result behaving the way both sides intended.

Only the commits you add on top of `HEAD` are kept; existing history must stay as it is and nothing is pushed. A pull request you cannot merge confidently is better left out than merged wrong.

Finish with `cli_result`: say what you merged and what you left out and why, with `exit_code` 0 if everything went in.
