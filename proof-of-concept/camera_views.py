"""Match calibrated broadcast layouts using background pixels, never identity."""
import json
from pathlib import Path

import numpy as np
from PIL import Image

from detect_handover import advance, clothing_crop, histogram

PROFILE_PATH = Path(__file__).with_name("camera-profiles") / "layouts.json"
BREAK_PROFILE_PATH = PROFILE_PATH.with_name("night-break.json")


def detect_break_scene(image, profile=None):
    """Match the calibrated break board and empty background, not a person."""
    if profile is None:
        profile = json.loads(BREAK_PROFILE_PATH.read_text())
    scores = {}
    for patch in profile["patches"]:
        actual = np.asarray(clothing_crop(image, patch["roi"]).resize((48,32)), dtype=float)/255
        difference = np.abs(actual.ravel()-np.asarray(patch["features"])).reshape(-1,3)
        score = float(difference.mean())
        if patch.get("ink_pixels"):
            score = max(score, float(difference[patch["ink_pixels"]].mean()))
        scores[patch["name"]] = score
    matched = all(scores[p["name"]] <= p["threshold"] for p in profile["patches"])
    return matched, {"matched": matched, "profile": profile["name"],
                     "distances": {k: round(v,4) for k,v in scores.items()},
                     "signal": "calibrated_break_sign"}


def luminance_histogram(crop):
    counts, _ = np.histogram(np.asarray(crop.convert("L")), bins=16, range=(0, 256))
    return counts / counts.sum()


def background_features(image, regions):
    return np.concatenate([
        np.asarray(clothing_crop(image, roi).resize((12, 8)), dtype=float).ravel()/255
        for roi in regions
    ])


def select_view(image, profiles):
    scores = sorted((float(np.abs(background_features(image, p["background_regions"])
                                  - np.asarray(p["background"])).mean()), p["name"])
                    for p in profiles)
    best, name = scores[0]
    known = best <= .10 and (len(scores) == 1 or scores[1][0]-best >= .025)
    return name if known else None, dict((name, round(score, 4)) for score, name in scores)


def baseline_geometry(config):
    # New garment features can be reconstructed from the original frame.
    # Timing/threshold changes must never acknowledge a new shirt implicitly.
    return [[{k: v for k, v in p.items() if k != 'garment_rois'}
             for p in config[0]], config[1]]


def observe(image, state, timestamp, observation_id, roi, threshold, confirm, interval,
            reset=False, profiles=None, evidence_root=None):
    if profiles is None:
        profiles = json.loads(PROFILE_PATH.read_text())
    view, distances = select_view(image, profiles)
    signature = [profiles, list(roi), threshold, confirm, interval]
    previous_config = state.get("config")
    # Polling frequency changes continuity limits, not clothing appearance.
    same_baselines = previous_config is not None and baseline_geometry(previous_config) == baseline_geometry(signature)
    if previous_config is not None and not same_baselines and not reset:
        return state, 'clothing_baseline_review_required', None, None, {
            'view': view, 'background_distances': distances,
            'baseline_review_required': True, 'reason': 'baseline_geometry_changed'}
    if reset or previous_config is None:
        state = {"config": signature, "views": {}}
    else:
        state = json.loads(json.dumps(state))
        state["config"] = signature
        if previous_config[2:-1] != signature[2:-1]:
            for saved in state['views'].values():
                saved.pop('tracking', None)
    details = {"view": view, "background_distances": distances}
    is_break, break_details = detect_break_scene(image)
    details["break_scene"] = break_details
    if is_break:
        for saved in state["views"].values():
            saved.pop("tracking", None)
        state["last_view"] = None
        details.update(view="night_break", scene_kind="scheduled_break")
        return state, "night_break_detected", None, None, details
    # Missing or uncertain layouts must not accumulate clothing evidence.
    if view is None:
        for saved in state["views"].values():
            saved.pop("tracking", None)
        state["last_view"] = None
        return state, "camera_view_unknown_review_required", None, None, details
    profile = next(p for p in profiles if p["name"] == view)
    crop_roi = roi if view == "presenter_main" else profile["clothing_roi"]
    crop = clothing_crop(image, crop_roi)
    features = histogram(crop)
    garment_rois = profile.get("garment_rois", [])
    garment_features = [histogram(clothing_crop(image, region)) for region in garment_rois]
    # Hands/phone often cover only the lower chest. Require agreement in the
    # upper half before treating a localized garment change as a new shirt.
    upper_rois = [[x1,y1,x2,(y1+y2)/2] for x1,y1,x2,y2 in garment_rois]
    upper_features = [histogram(clothing_crop(image, region)) for region in upper_rois]
    anchor_rois = [[(x1+x2)/2,y1,x2,y1+(y2-y1)/4] for x1,y1,x2,y2 in garment_rois]
    anchor_features = [histogram(clothing_crop(image, region)) for region in anchor_rois]
    details["clothing_roi"] = list(crop_roi)
    details["garment_rois"] = garment_rois
    saved = state["views"].get(view)
    if saved is None:
        saved = {"baseline": features.tolist(), "baseline_observation": observation_id,
                 "luminance_baseline": luminance_histogram(crop).tolist(),
                 "garment_baselines": [feature.tolist() for feature in garment_features],
                 "garment_upper_baselines": [feature.tolist() for feature in upper_features],
                 "garment_anchor_baselines": [feature.tolist() for feature in anchor_features],
                 "garment_rois": garment_rois}
        state["views"][view] = saved
        status, score = "baseline_created", 0
    else:
        if state.get("last_view") != view:
            saved.pop("tracking", None)
        old_profile = next(p for p in previous_config[0] if p['name'] == view)
        needs_garments = (len(saved.get('garment_baselines', [])) != len(garment_rois)
                          or len(saved.get('garment_upper_baselines', [])) != len(garment_rois)
                          or len(saved.get('garment_anchor_baselines', [])) != len(garment_rois)
                          or saved.get('garment_rois', old_profile.get('garment_rois', [])) != garment_rois)
        if needs_garments:
            reference = (Path(evidence_root) / saved['baseline_observation'] / 'frame.jpg'
                         if evidence_root is not None else None)
            if reference is None or not reference.exists():
                details.update(baseline_review_required=True,
                               reason='original_garment_baseline_frame_unavailable',
                               baseline_observation=saved['baseline_observation'])
                return state, 'clothing_baseline_review_required', None, crop, details
            with Image.open(reference) as original:
                saved['garment_baselines'] = [histogram(clothing_crop(original, region)).tolist()
                                              for region in garment_rois]
                saved['garment_upper_baselines'] = [histogram(clothing_crop(original, region)).tolist()
                                                    for region in upper_rois]
                saved['garment_anchor_baselines'] = [histogram(clothing_crop(original, region)).tolist()
                                                     for region in anchor_rois]
            saved['garment_rois'] = garment_rois
            details['garment_baseline_reconstructed'] = True
        score = float(np.abs(features-np.asarray(saved["baseline"])).sum()/2)
        # Hydrate older state from its original evidence, never from a new shirt.
        if "luminance_baseline" not in saved and evidence_root is not None:
            reference = Path(evidence_root) / saved["baseline_observation"] / "frame.jpg"
            if reference.exists():
                with Image.open(reference) as original:
                    saved["luminance_baseline"] = luminance_histogram(
                        clothing_crop(original, crop_roi)).tolist()
        luminance_score = None
        if "luminance_baseline" in saved:
            luminance_score = float(np.abs(luminance_histogram(crop) -
                                          np.asarray(saved["luminance_baseline"])).sum()/2)
        details["colour_score"] = score
        details["luminance_score"] = luminance_score
        garment_baselines = saved.get("garment_baselines", [])
        garment_scores = [float(np.abs(feature-np.asarray(reference)).sum()/2)
                          for feature, reference in zip(garment_features, garment_baselines)]
        details["garment_scores"] = garment_scores
        upper_scores = [float(np.abs(feature-np.asarray(reference)).sum()/2)
                        for feature,reference in zip(upper_features,saved.get('garment_upper_baselines',[]))]
        anchor_scores = [float(np.abs(feature-np.asarray(reference)).sum()/2)
                         for feature,reference in zip(anchor_features,saved.get('garment_anchor_baselines',[]))]
        # Calibrated upper-right anchor avoids the hand/phone path. Its smaller
        # print coverage needs two-thirds of the full-patch threshold.
        effective_scores = [min(full,upper,anchor*1.5)
                            for full,upper,anchor in zip(garment_scores,upper_scores,anchor_scores)]
        details['garment_upper_scores'] = upper_scores
        details['garment_anchor_scores'] = anchor_scores
        details['garment_effective_scores'] = effective_scores
        details['localized_garment_change_ignored'] = any(full>=threshold and effective<threshold
                                                        for full,effective in zip(garment_scores,effective_scores))
        # A shirt print can disappear while a plain shoulder crop stays black.
        # The additional regions remain clothing-only and use the same
        # persistence, layout-continuity and review rules as the primary crop.
        score = max([score] + effective_scores)
        # Fine brightness bins distinguish light grey from white. This remains
        # an appearance cue: lighting changes can also require review.
        if luminance_score is not None and luminance_score >= .60:
            score = max(score, threshold)
        tracking = advance(saved.get("tracking", {}), score, timestamp, threshold,
                           confirm, max_gap=max(interval*2.5, 25))
        saved["tracking"] = tracking
        details["tracking"] = tracking
        status = tracking["status"]
    state["last_view"] = view
    details["baseline_observation"] = saved["baseline_observation"]
    return state, status, score, crop, details
