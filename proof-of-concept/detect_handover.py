#!/usr/bin/env python3
"""Flag sustained clothing changes for human review, without recognizing people.

Run: python detect_handover.py BASELINE FRAME --state state.json
Repeat for timestamped frames, normally every 10 seconds. Explicitly replace
BASELINE and reset the state after a human confirms a handover or camera change.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np
from PIL import Image


# Clothing-only region for the calibrated presenter-main view.
DEFAULT_ROI = (.48, .51, .65, .62)


def clothing_crop(image, roi):
    x0, y0, x1, y1 = roi
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("ROI must be normalized left,top,right,bottom coordinates")
    return image.convert("RGB").crop((round(x0*image.width), round(y0*image.height),
                                      round(x1*image.width), round(y1*image.height)))


def histogram(crop):
    hsv = np.asarray(crop.resize((160, 80)).convert("HSV"), dtype=float)
    # Hue does not describe white/grey/black fabric. Collapse it for low
    # saturation so JPEG hue noise in white shirts does not dominate.
    hsv[:, :, 0][hsv[:, :, 1] < 48] = 0
    hist, _ = np.histogramdd(hsv.reshape(-1, 3), bins=(12, 4, 4),
                             range=((0, 256), (0, 256), (0, 256)))
    return hist.ravel() / hist.sum()


def compare(baseline, current, roi=DEFAULT_ROI):
    before, after = clothing_crop(baseline, roi), clothing_crop(current, roi)
    # Total variation: 0 = identical colour distributions, 1 = disjoint.
    score = float(np.abs(histogram(before)-histogram(after)).sum()/2)
    return score, before, after


def advance(state, score, timestamp, threshold=.45, confirm_seconds=60, max_gap=25):
    previous = state.get("last_observation")
    if previous is not None and timestamp <= previous:
        raise ValueError("Observation timestamps must increase")
    reset = previous is None or timestamp-previous > max_gap
    since = None if reset else state.get("changed_since")
    if score < threshold:
        since = None
    elif since is None:
        since = timestamp
    duration = 0 if since is None else timestamp-since
    return {
        "last_observation": timestamp,
        "changed_since": since,
        "sustained_seconds": duration,
        "status": ("possible_handover_review_required" if since is not None
                   and duration >= confirm_seconds else
                   "change_pending" if since is not None else
                   "no_clothing_change_detected"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("frame", type=Path)
    parser.add_argument("--state", type=Path, default=Path("proof-of-concept/handover/state.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("proof-of-concept/handover"))
    parser.add_argument("--timestamp", help="Frame capture time in ISO 8601 with timezone")
    parser.add_argument("--roi", type=float, nargs=4, default=DEFAULT_ROI)
    parser.add_argument("--threshold", type=float, default=.45)
    parser.add_argument("--confirm-seconds", type=float, default=60)
    args = parser.parse_args()
    timestamp = datetime.fromisoformat(args.timestamp) if args.timestamp else datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        parser.error("--timestamp needs a timezone")
    if not 0 < args.threshold <= 1 or args.confirm_seconds <= 0:
        parser.error("threshold must be in (0,1] and confirm-seconds must be positive")
    from camera_views import observe, select_view
    from camera_views import PROFILE_PATH
    baseline, frame = Image.open(args.baseline), Image.open(args.frame)
    profiles = json.loads(PROFILE_PATH.read_text())
    baseline_view, _ = select_view(baseline, profiles)
    current_view, _ = select_view(frame, profiles)
    config = {"baseline": str(args.baseline.resolve()),
              "baseline_modified": args.baseline.stat().st_mtime_ns,
              "roi": list(args.roi), "threshold": args.threshold,
              "confirm_seconds": args.confirm_seconds}
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    if state.get("config") != config:
        state = {}
    source = {"path": str(args.frame.resolve()), "modified": args.frame.stat().st_mtime_ns}
    if state.get("source") == source:
        parser.error("This saved frame was already observed; capture a fresh frame")
    # A CLI baseline must be from the same calibrated view as the new frame.
    model = state.get("model", {})
    if not model:
        model, _, _, _, _ = observe(baseline, {}, timestamp.timestamp()-1,
                                   "manual-baseline", args.roi, args.threshold,
                                   args.confirm_seconds, 10, profiles=profiles)
    if baseline_view is None or current_view != baseline_view:
        model = {}
        status, score, after = "camera_view_changed_review_required", None, None
        details = {"baseline_view": baseline_view, "current_view": current_view}
    else:
        model, status, score, after, details = observe(
            frame, model, timestamp.timestamp(), "current", args.roi,
            args.threshold, args.confirm_seconds, 10, profiles=profiles)
    state = {"config": config, "source": source, "model": model,
             "status": status, "score": score, "camera_view": details,
             "captured_at": timestamp.isoformat(),
             "note": "Clothing-change cue only; does not establish identity or presence."}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    # Remove stale comparisons when the views cannot be compared.
    for name in ("baseline-clothing.png", "current-clothing.png", "comparison.png"):
        (args.output_dir / name).unlink(missing_ok=True)
    if after is not None:
        profile = next(p for p in profiles if p["name"] == baseline_view)
        roi = args.roi if baseline_view == "presenter_main" else profile["clothing_roi"]
        before = clothing_crop(baseline, roi)
        before.save(args.output_dir / "baseline-clothing.png")
        after.save(args.output_dir / "current-clothing.png")
        preview = Image.new("RGB", (640, 160))
        preview.paste(before.resize((320, 160)), (0, 0))
        preview.paste(after.resize((320, 160)), (320, 0))
        preview.save(args.output_dir / "comparison.png")
    output = json.dumps(state, indent=2)
    args.state.write_text(output + "\n")
    (args.output_dir / "result.json").write_text(output + "\n")
    print(output)


if __name__ == "__main__":
    main()
