# Reviewed hands/phone occlusion, 2026-10-26

These original JPEG frames and their manifest preserve 17 false garment-change
cues from 06:29–06:37 UTC (07:29–07:37 Europe/Warsaw). The user confirmed no person
change during this interval. The baseline observation is from the existing same
stint. Original frame IDs, timestamps, accepted counters and SHA-256 hashes are in
manifest.json; the images are copied unchanged from anomaly evidence.

Hands and the phone cover the lower chest patch while the primary clothing region
stays similar. One frame also covers most of the upper half. Supplemental garment
cues now need spatial corroboration from the upper half and a calibrated upper-right
anchor (the anchor needs two-thirds of the full patch's difference threshold).
This is a calibrated appearance heuristic, not reliable identity recognition or
general-purpose hand segmentation. Genuine unknown scenes and ambiguous brief
appearance cues still require review. Tests also preserve the recorded graphic-to-
plain-black-shirt handover and its original start/baseline timing.

Regression: proof-of-concept/test_clothing_occlusion.py.

An additional 30 later pending-cue frames are preserved unchanged in
followup-manifest.json, bringing regression coverage to 47 obstruction frames.
Both manifests retain independent SHA-256 hashes; the first archive is unchanged.

The `raised-arms/` archive contains the original beige-shirt baseline and 18
consecutive samples around two isolated arm-over-shirt cues at 08:38:40 and
08:40:02 Europe/Warsaw. Its own manifest preserves timestamps, counter values and
SHA-256 hashes. Broad colour/brightness changes in the main view now require
corroboration from a separate left-shoulder patch (.405,.47,.435,.51). Print-only
dark-shirt changes keep the existing garment checks. The shoulder feature is
reconstructed from the original baseline on upgrade; missing original evidence
requires review. This broadcast-specific heuristic does not segment hands or
prove identity, and ambiguous cues remain reviewable.
