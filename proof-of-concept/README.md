# Counter recognition proof of concept

The saved livestream frame is `live-frame.jpg`. The script automatically finds
an aligned group of red digits, crops each illuminated digit, measures seven
segment regions, and matches the resulting pattern against digits 0–9.
It does not call a remote AI service or use a fixed counter location.

Run from the repository root with Python 3. The root `requirements.txt`
installs both image-analysis and supervisor/test dependencies:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python proof-of-concept/read_counter.py proof-of-concept/live-frame.jpg
```

The `recognition/` directory contains the JSON result, an annotated source
frame, an enlarged counter crop, and the red detection mask. Bounding boxes
use `[left, top, right, bottom]` image pixel coordinates, with exclusive right
and bottom edges. Unknown segment patterns produce `?`, a null value, and
exit status 2; failure to locate a counter raises an error.

On the captured 1920×1080 frame, the script reads **58857**, at bounding box
`[421, 381, 741, 452]`, matching visual inspection.

Digit count is variable, with no five- or six-digit cap. Synthetic image
checks cover 999,999 → 1,000,000, 1,000,001, 1,234,567, 1,999,999 and
10,000,000, including narrow 1 digits. Run them with:

```sh
.venv/bin/python -m unittest discover -s proof-of-concept -p 'test_*.py'
```

The original proof-of-concept validation used this one frame; later captured
layouts and transitions are preserved under `test-data/`. Red thresholds, segment regions,
and component grouping need validation against more live frames, lighting,
camera motion, and digit transitions. Synthetic checks do not establish
accuracy on real stream footage, especially with partially lit digits.
The standalone recognizer has no temporal validation or capture loop. The
root supervisor captures continuously and validates counter changes over time.

## Possible presenter handover cues

`detect_handover.py` compares clothing colour distributions in a fixed torso
region that excludes the head in a calibrated camera composition. It does not
recognize faces, build biometric profiles, establish identity, or infer that
someone is present. It recognizes the saved presenter-main and overhead-main layouts using background
patches and selects the torso in the main view or inset respectively. Unknown
layouts or a baseline from a different view return a camera-review status,
without comparing clothing. Supply a baseline from the same view. Other
compositions need calibration in `camera-profiles/layouts.json`.

```sh
.venv/bin/python proof-of-concept/detect_handover.py \
  proof-of-concept/live-frame.jpg proof-of-concept/latest/live-frame.jpg \
  --timestamp 2026-10-25T20:21:02+01:00
```

For standalone clothing checks, pass a newly captured frame about every 10 seconds
and its capture timestamp. The script saves comparison crops and a JSON
state, rejecting a repeated saved frame. A colour-distance score of at least
0.45, or a 16-bin brightness-distance score of at least 0.60, must persist for
60 seconds, with gaps no longer than 25 seconds, to
produce `possible_handover_review_required`. One changed frame is only
`change_pending`. These are provisional thresholds to calibrate on actual
handover footage. Human confirmation is always required; after confirmation,
choose a fresh baseline and reset the state file. It never silently adopts
an unconfirmed change as its baseline.

Long capture gaps, including an overnight pause, reset pending evidence.
The standalone CLI has no hourly or night schedule. The root supervisor has
configurable night-inactivity handling and a calibrated break-board scene;
see [night stop times](../README.md#night-stop-times). An hour passing is not evidence of a handover.
An empty chair, a title card, camera movement, lighting changes, or a shirt
change can also trigger this cue. Two presenters in similar clothes can be
missed. The supervisor in the repository root provides continuous capture, separate
per-view baselines, and resets pending evidence at a camera transition.

On the two captured real frames, the score is approximately 0.001 and the
result is `no_clothing_change_detected`. This is not a claim of matching
identity. Synthetic tests verify a white/black clothing difference,
persistence, time ordering, and resetting after long gaps. Verification
against a real handover remains outstanding.
