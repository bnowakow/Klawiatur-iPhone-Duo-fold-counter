# Same-channel command delay — 2026-10-26

Only incoming commands authored by the current bot posting channel wait 60
seconds after receipt. Other viewers use the immediate path. Fresh text is
generated at due time, subject to the original <=120-second command age and
all existing guards. Pending schedules are durable, claimed once and not
retained beyond expiry. No rejected/throttled historical event is revived.

The real owner identity was confirmed against command events 23/89/131/155/156.
The cooldown and rejection streak were reset after deployment, with a DB incident
record. The obsolete reminder for the earlier cooldown was deleted.
44 YouTube unit tests and both PostgreSQL integration checks passed. All test
sends were simulated; no live public test command was posted by the agent.
