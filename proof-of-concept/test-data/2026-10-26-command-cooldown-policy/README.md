# One-minute command cooldown trial — 2026-10-26

The user requested 60-second per-command cooldowns, reverting to 600 seconds
on a new provider posting rateLimitExceeded. The monitored channel policy is
persisted in youtube_posting_policies. Reversion changes caps/disclaimers in
one transaction, audits the triggering API request and logs stdout ERROR.
Normal account backoff remains separate and rejected replies are not retried.
The existing 60-second same-posting-channel reply delay remains active.

48 YouTube unit tests and four PostgreSQL integration checks passed. The
positive rollback check uses session-local table copies; it never changes
live command policy or posts to Google. Activation changes configuration only,
not measurement data. Reinitialization preserved the active 60-second values.

The preceding privacy change was deployed too: !rekord is numeric only, public
send validation rejects internal appearance fields, and descriptions remain
in PostgreSQL/Sheets. activation.json contains a dry preview and internal row
count. No public test message was sent during implementation or deployment.
