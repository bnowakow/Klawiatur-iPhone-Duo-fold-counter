# Adaptive polling and full-path cadence evidence

Keep this dataset unchanged. Four 60-second serial/full-production-path tests
compare 3/4-second start-to-start targets. Regular monitoring remained active.

Pre-fix results exposed PostgreSQL deadlocks caused by per-cycle schema/view
recreation held through capture. They are preserved, not used as a hardware
limit. After committing schema setup separately and caching success, the
corrected runs recorded zero database errors:

| Target | Cycles | Mean | p95 | Max | Overruns |
| --- | --- | --- | --- | --- | --- |
| 3 s | 20 | 2.52 s | 2.99 s | 3.15 s | 1 |
| 4 s | 15 | 2.86 s | 3.24 s | 3.62 s | 0 |

The selected base is 4 seconds with adaptive backpressure. These short
sequential tests do not prove future reliability or a hard CPU limit.
Results, source frame/observation checksums and logs are retained here; original
frames remain under each evidence manifest's data/benchmarks/ directory,
outside normal retention. Results and benchmark observations are also in
PostgreSQL. Back up those original directories with the database.

Integration results under integration/ verify schema commit/lock release,
durable scheduling state, queued database-outage replay and observation links.
Unit tests cover overrun growth, stable recovery, saturation cooldown, failure
backoff, restart persistence and manual base changes. See docs/backpressure.md.

`live-polling-events.json` preserves production evidence of adaptive behavior:
a real 18.78-second cycle raised the 10-second budget to 23.5 seconds. The
4-second base deployment preserved that raised budget; after 12 stable cycles
it recovered to 21.2 seconds. The base is a floor, not a claim that every live
cycle is currently scheduled at four seconds. Continued observations govern
recovery. The final suite contains 58 passing tests, and scheduler/session
PostgreSQL integration checks passed.
