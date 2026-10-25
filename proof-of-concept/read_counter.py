#!/usr/bin/env python3
"""Locate and decode a red seven-segment counter in a still image.

Usage: python3 proof-of-concept/read_counter.py IMAGE [--output-dir DIRECTORY]
Requires numpy and Pillow. This proof of concept expects at least three lit
digits, separated by dark space, with broadly similar size and alignment.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


# Segment order: top, upper right, lower right, bottom, lower left,
# upper left, middle. Unknown patterns are reported, never guessed.
DIGITS = {
    "1111110": "0", "0110000": "1", "1101101": "2",
    "1111001": "3", "0110011": "4", "1011011": "5",
    "1011111": "6", "1110000": "7", "1111111": "8",
    "1111011": "9",
}
REGIONS = [
    (.2, 0, .8, .18), (.65, .18, 1, .42), (.65, .58, 1, .82),
    (.2, .82, .8, 1), (0, .58, .35, .82), (0, .18, .35, .42),
    (.2, .4, .8, .6),
]


def components(mask):
    """Four-connected bounding boxes; no OpenCV dependency needed."""
    remaining = mask.copy()
    height, width = mask.shape
    for y, x in zip(*np.where(mask)):
        if not remaining[y, x]:
            continue
        remaining[y, x] = False
        pending = [(int(y), int(x))]
        left = right = int(x)
        top = bottom = int(y)
        while pending:
            yy, xx = pending.pop()
            left, right = min(left, xx), max(right, xx)
            top, bottom = min(top, yy), max(bottom, yy)
            for ny, nx in ((yy-1, xx), (yy+1, xx), (yy, xx-1), (yy, xx+1)):
                if 0 <= ny < height and 0 <= nx < width and remaining[ny, nx]:
                    remaining[ny, nx] = False
                    pending.append((ny, nx))
        yield (left, top, right + 1, bottom + 1)


def locate_counter(mask):
    # Join individual segments while retaining the gaps between digits.
    kernel = max(3, round(mask.shape[0] / 1080 * 11)) | 1
    joined = np.asarray(Image.fromarray(mask).filter(ImageFilter.MaxFilter(kernel)))
    candidates = []
    for box in components(joined):
        x0, y0, x1, y1 = box
        h, w = y1 - y0, x1 - x0
        if .023 * mask.shape[0] <= h <= .14 * mask.shape[0] and .12 <= w/h <= .95:
            candidates.append(box)
    groups = []
    for seed in candidates:
        sh = seed[3] - seed[1]
        cy = (seed[1] + seed[3]) / 2
        aligned = sorted(
            (b for b in candidates
             if .55 <= (b[3]-b[1])/sh <= 1.8
             and abs((b[1]+b[3])/2-cy) < .2*sh),
            key=lambda b: b[0],
        )
        runs = [[]]
        for box in aligned:
            if runs[-1] and box[0] - runs[-1][-1][2] > sh:
                runs.append([])
            runs[-1].append(box)
        groups.extend(run for run in runs if len(run) >= 3)
    if not groups:
        raise ValueError("No aligned red counter with at least three digits found")
    return max(groups, key=lambda group: (len(group), sum(b[3]-b[1] for b in group)))


def recognize(image):
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    r, g, b = rgb.transpose(2, 0, 1)
    strict = (r > 170) & (r-g > 65) & (r-b > 45) & (g < 160)
    relaxed = (r > 140) & (r-g > 45) & (r-b > 30) & (g < 210)
    boxes = locate_counter(strict)
    readings = []
    for x0, y0, x1, y1 in boxes:
        ys, xs = np.where(strict[y0:y1, x0:x1])
        left, top = x0 + int(xs.min()), y0 + int(ys.min())
        right, bottom = x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1
        digit = relaxed[top:bottom, left:right]
        h, w = digit.shape
        occupancies = []
        for xa, ya, xb, yb in REGIONS:
            region = digit[int(ya*h):max(int(ya*h)+1, int(yb*h)),
                           int(xa*w):max(int(xa*w)+1, int(xb*w))]
            occupancies.append(float(region.mean()))
        readings.append({
            "bbox": [left, top, right, bottom],
            "segment_occupancy": [round(v, 3) for v in occupancies],
            "_narrow": w / h < .3,
        })
    # Camera/exposure changes alter LED stroke thickness. Calibrate horizontal
    # and vertical occupancy separately across this counter, with noise floors.
    horizontal = max(d["segment_occupancy"][i] for d in readings for i in (0,3,6))
    vertical = max(d["segment_occupancy"][i] for d in readings for i in (1,2,4,5))
    thresholds = [max(.12,horizontal*.45) if i in (0,3,6) else max(.04,vertical*.35)
                  for i in range(7)]
    for digit in readings:
        pattern = "".join("1" if value >= threshold else "0" for value, threshold
                          in zip(digit["segment_occupancy"], thresholds))
        if digit.pop("_narrow"):
            pattern = "0110000"
        digit.update(digit=DIGITS.get(pattern, "?"), segments=pattern)
    text = "".join(item["digit"] for item in readings)
    bbox = [min(d["bbox"][0] for d in readings), min(d["bbox"][1] for d in readings),
            max(d["bbox"][2] for d in readings), max(d["bbox"][3] for d in readings)]
    return {"text": text, "value": int(text) if "?" not in text else None,
            "bbox": bbox, "digits": readings,
            "segment_thresholds": [round(t,3) for t in thresholds]}, strict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("proof-of-concept/recognition"))
    args = parser.parse_args()
    image = Image.open(args.image).convert("RGB")
    result, mask = recognize(image)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    image.crop(result["bbox"]).resize((960, 210)).save(args.output_dir / "counter-crop.png")
    Image.fromarray(mask).save(args.output_dir / "red-mask.png")
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    for digit in result["digits"]:
        draw.rectangle(digit["bbox"], outline="lime", width=2)
    x, y, _, _ = result["bbox"]
    draw.text((x, max(0, y-35)), "Counter: " + result["text"], fill="lime", font_size=26)
    annotated.save(args.output_dir / "annotated-frame.jpg")
    result["source"] = str(args.image)
    output = json.dumps(result, indent=2)
    (args.output_dir / "result.json").write_text(output + "\n")
    print(output)
    return 0 if result["value"] is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
