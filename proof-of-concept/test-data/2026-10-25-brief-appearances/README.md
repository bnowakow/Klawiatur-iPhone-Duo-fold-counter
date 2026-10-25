# Brief appearances, 25 October 2026

Original JPEG bytes copied from live observations; SHA-256 hashes and observation
IDs are in manifest.json. Times are Europe/Warsaw.

- 20:59:53: white T-shirt, counter 60,704.
- 21:01:23: olive jacket over dark layers, counter 60,749; change pending.
- 21:01:53: camera uncertain; interrupted the pending cue.
- 21:02:52: gray sweatshirt, counter 60,751; coarse colour score 0.275.
- 21:03:53: gray sweatshirt, counter 60,753; coarse colour score 0.023.

The user clarified that the brief presenter appearance did not start folding.
The automated record supports only no accepted counter increase during that
appearance: slow/rejected raw readings do not prove zero folds. Preserve this
distinction. Clothing appearance cues are review requests, not identity matches.

Regression: preserve the short interrupted appearance separately; fine brightness
bins flag gray versus white while the coarse histogram misses it. Persistence
still requires continuous observations in a known layout. Synthetic repeated
frames in the camera unit test exercise the timer only, not live freshness.
