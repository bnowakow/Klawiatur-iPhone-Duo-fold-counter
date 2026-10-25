# Real night-break regression evidence, 2026-10-25

These nine original JPEG captures and `observations.json` preserve the transition
near 22:00 Warsaw time and the later explicit break scene. Original observation
metadata includes the failures that prompted this fix; do not rewrite it with
new OCR results. Some transition frames contain occluded digits and are not
expected to produce a complete reading.

The reference `01ab91c9-43ef-4f55-8d1d-244bbea6990c.jpg` (22:08:28 Warsaw) visibly
shows counter **63,333**, an empty table, and the board reading “PRZERWA DO 6:00 /
BREAK UNTIL 6:00 AM (UTC+1)”. Its sign patch, selected blue-ink pixels, and empty
background patch calibrate the detector. `81fb9ca9-4b8c-4bc5-92f6-4ac68f315b1e.jpg`
(22:08:12) is a separate positive verification frame. Both are checked by tests;
every other captured transition frame must fail the break-scene match.

`labels.json` records manually checked break labels and counter values where
clearly legible. `calibration.json` freezes the original profile; the active
profile lives in `../../camera-profiles/night-break.json`. `unit-tests.txt`
records the first passing suite after adding the real-frame tests. The checksum
manifest covers this immutable evidence; add new dated captures separately.

The runtime detector matches this calibrated board appearance. It does not
perform general text OCR, recognize arbitrary signs, or identify people. A
blanked board and an obstructed background are negative regression cases.

The counter's thin LED strokes exposed a fixed-threshold decoding failure.
Horizontal and vertical segment occupancies now have separate adaptive
thresholds and noise floors. The final three folds during the transition are
not automatically assigned to the preceding anonymous presenter stint.
