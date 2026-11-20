I just pushed the branch in `/app`, and CI is running on it (`ci --help`).
Babysit it until it's green.
If a job fails, fix the cause and start a new run with `ci rerun`.

When a run is fully green, write its run number to `/app/answer.txt`.
