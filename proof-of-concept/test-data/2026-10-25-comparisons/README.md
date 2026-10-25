# Comparison and cadence evidence

Preserve this dataset unchanged. It contains the regression results, isolated
PostgreSQL comparison tests, laptop metadata, Docker resource samples and three
60-media-second live benchmarks on 2026-10-25. Regular monitoring remained active.

| Persistent capture target | Frames | Analysis + DB mean | p95 | Max | Cycles over target |
| --- | --- | --- | --- | --- | --- |
| 1 second | 60/60 | 0.79 s | 1.55 s | 3.33 s | 6 |
| 2 seconds | 30/30 | 0.91 s | 2.83 s | 4.06 s | 2 |
| 5 seconds | 12/12 | 1.04 s | 1.54 s | 3.64 s | 0 |

The laptop reports MacBookPro18,2, 10 logical CPUs and 64 GiB host memory;
Docker reported about 7.65 GiB available. Benchmark CPU averaged 0.67, 0.41
and 0.25 cores respectively, excluding CPU time used in other containers.
The resource snapshot during the 1-second run showed about 398 MiB for its
container. These are observations, not whole-laptop peak resource limits.

The current single-frame capture path was separately timed twice at 2.03 and
2.09 seconds, before analysis. Persistent capture avoids repeating this setup.
HLS segment delivery is bursty; media sampling does not guarantee fresh images
at that wall-clock frequency. The runs processed all requested frames but had
56/28/10 accepted counter samples, respectively; all raw frames and rejected or
unreadable observations are preserved.

Five seconds is a practical tested starting candidate; these short sequential
runs do not establish a production reliability threshold, thermal stability,
or handover accuracy. The regular app remains at its configured 10 seconds.
No benchmark went below one second. The one-second run also provides an offline
common reference for thinning to 2/5/10/20/30/60 seconds; the thinning errors
are in its result. Those errors do not establish true OCR accuracy.

Each `interval-…-result.json` is a copied benchmark report; the matching evidence
manifest gives the original retained directory and checksums of every frame,
crop and observation log. Original evidence remains under `data/benchmarks/`
and is outside normal retention. Results are also in PostgreSQL
`frame_interval_benchmarks`; sampled observations use `benchmark:…` stream keys.
Back up the original evidence directories as well as this dataset/database.

`integration/` preserves isolated comparison-test results. The final regression
suite has 50 passing tests, including unsupported fine-cadence handling.
