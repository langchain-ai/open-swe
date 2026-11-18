Schedule the bound recovery thread to investigate again in 1–60 minutes.
Replaces its previous scheduled follow-up.
Use reasonable backoff (for example 5, 15, 30, then 60 minutes) while an outage is unchanged.
These investigations do not spend validation attempts and continue for Robin's own pause even after the original batch expires.
No shell polling loop or separate automation is needed.
Ordinary waiting is not an escalation condition.
