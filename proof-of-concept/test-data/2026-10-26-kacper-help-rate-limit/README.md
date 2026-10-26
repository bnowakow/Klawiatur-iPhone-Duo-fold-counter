# Kacper !pomoc rejection and automatic rollback

Observed 26 October 2026. The real request/delivery records confirm event 285
was rejected with rateLimitExceeded. This was a different viewer from the bot
posting channel, so no own-channel reply delay applied. The automatic policy
rollback restored all commands from 60 to 600 seconds. incident.json contains
exact timestamps, request links, preserved prepared text and rollback metadata.
The snapshot is also in PostgreSQL under
youtube-kacper-help-rate-limit-2026-10-26-event-285. No public message was posted
to investigate/document this incident. No measurement records were corrected.
