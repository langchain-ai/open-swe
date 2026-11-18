Clear only this recovery thread's exact current outage pause, using pause_id from fresh merge_queue_recovery_status and a reason describing observed recovery.
A human pause or another incident's pause is rejected.
This resumes normal validation of eligible work; it cannot revive expired attempts, extend deadlines, requeue ejected PRs, or publish success.
