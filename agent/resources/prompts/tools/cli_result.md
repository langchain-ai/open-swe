Supply this run's result to the terminal that started the thread. The CLI prints `stdout` verbatim as its only output, then exits with `exit_code`. Nothing else you write reaches that terminal.

Call it as the last thing you do in every run, including runs that fail and runs that only answer a question. `stdout` is the complete answer, written for a terminal: plain text rather than Markdown, unless the user asked for a format. `exit_code` is 0 when the task succeeded and nonzero, usually 1, when it failed or could not be done.
