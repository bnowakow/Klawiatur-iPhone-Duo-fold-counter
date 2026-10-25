# Preserved test evidence

Keep these datasets unchanged. Do not overwrite, regenerate in place, or delete
old frames, crops, masks, results, or calibration snapshots when adjusting the
detector. Add a new dated directory for later captures and test results.

`2026-10-25-camera-angles/` preserves all 23 image/JSON evidence files available
during the initial camera-layout check, including the user's previous-angle
attachment, both fresh overhead-main captures, derived crops and results, and
the original calibration. `manifest.json` records SHA-256 checksums. These
files are outside the supervisor's retention cleanup and are not Git-ignored.
They are historical evidence; active calibration remains in `camera-profiles/`.

When a new angle appears:

1. Retain the raw captures and observation metadata. The running supervisor
   archives every unknown-view observation in `data/camera-review/<id>/`;
   ordinary frame cleanup does not remove this archive.
2. Copy representative captures and metadata into a new dated dataset here.
   Record the expected layout and manually verified clothing-only coordinates.
3. Add a camera profile and regression cases for the new layout, unknown views,
   and transitions to existing layouts. Never infer a handover from a switch.
4. Run all tests, including existing-angle cases. Preserve the new results and
   annotated crops alongside their source frames.

Detection of an unknown view preserves evidence and requests review. Algorithm
changes require a subsequent coding session; the supervisor does not edit its
own code. The review archive is local runtime data, so include it in backups
before replacing the host or deleting `data/`.
