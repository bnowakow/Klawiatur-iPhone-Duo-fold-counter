"""Anonymous presenter stints inferred from sustained clothing-change cues."""
from datetime import datetime, timezone
import json
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS presenter_sessions (
 id uuid PRIMARY KEY, stream_url text NOT NULL,
 detected_at timestamptz NOT NULL, confirmed_at timestamptz NOT NULL,
 detection_reason text NOT NULL, first_observation_id uuid NOT NULL,
 folding_started_at timestamptz, folding_start_counter bigint,
 folding_start_observation_id uuid,
 baseline_counter bigint, baseline_counter_at timestamptz,
 last_counter bigint, last_counter_at timestamptz,
 ended_at timestamptz, end_reason text,
 version_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS presenter_sessions_stream_time
 ON presenter_sessions(stream_url, detected_at DESC);
CREATE TABLE IF NOT EXISTS presenter_appearances (
 id uuid PRIMARY KEY, stream_url text NOT NULL,
 detected_at timestamptz NOT NULL, version_at timestamptz NOT NULL,
 status text NOT NULL, details jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS presenter_handover_corrections (
 id uuid PRIMARY KEY, stream_url text NOT NULL, corrected_at timestamptz NOT NULL,
 old_session_id uuid NOT NULL REFERENCES presenter_sessions(id),
 new_session_id uuid NOT NULL REFERENCES presenter_sessions(id), details jsonb NOT NULL
);
CREATE OR REPLACE VIEW presenter_session_rates AS
 SELECT *,
   last_counter - baseline_counter AS folds_since_detection_baseline,
   last_counter - folding_start_counter AS folds_since_folding_start,
   EXTRACT(EPOCH FROM (COALESCE(ended_at, last_counter_at) - detected_at))
      AS nominal_seconds,
   EXTRACT(EPOCH FROM (last_counter_at - folding_started_at)) AS folding_seconds,
   (last_counter - baseline_counter) * 3600.0 /
     NULLIF(EXTRACT(EPOCH FROM (COALESCE(ended_at, last_counter_at) - detected_at)), 0)
      AS nominal_folds_per_hour,
   (last_counter - folding_start_counter) * 3600.0 /
     NULLIF(EXTRACT(EPOCH FROM (last_counter_at - folding_started_at)), 0)
      AS folding_folds_per_hour
 FROM presenter_sessions;
"""

# Applied after the observations table exists (supervisor owns schema setup).
SCHEMA += """
CREATE OR REPLACE VIEW presenter_recent_pace AS
 SELECT s.id AS session_id, s.stream_url, s.detected_at,
        s.folding_started_at, s.last_counter_at AS measured_at,
        180 AS target_window_seconds, samples.sample_count,
        samples.first_counter, s.last_counter,
        EXTRACT(EPOCH FROM (s.last_counter_at - samples.first_at)) AS measured_seconds,
        (s.last_counter - samples.first_counter) * 60.0 /
          NULLIF(EXTRACT(EPOCH FROM (s.last_counter_at - samples.first_at)), 0)
          AS recent_folds_per_minute,
        rates.folding_folds_per_hour / 60 AS stint_folds_per_minute
 FROM presenter_sessions s
 JOIN presenter_session_rates rates ON rates.id = s.id
 CROSS JOIN LATERAL (
   SELECT count(*) AS sample_count, min(o.observed_at) AS first_at,
          (array_agg(o.accepted_counter ORDER BY o.observed_at))[1] AS first_counter
   FROM observations o
   WHERE o.stream_url = s.stream_url AND o.accepted_counter IS NOT NULL
     AND o.observed_at >= GREATEST(s.detected_at, s.last_counter_at - interval '3 minutes')
     AND o.observed_at <= s.last_counter_at
 ) samples
 WHERE s.ended_at IS NULL;
"""

COLUMNS = ("id", "stream_url", "detected_at", "confirmed_at", "detection_reason",
           "first_observation_id", "folding_started_at", "folding_start_counter",
           "folding_start_observation_id", "baseline_counter", "baseline_counter_at",
           "last_counter", "last_counter_at", "ended_at", "end_reason", "version_at")

SCHEMA += """
CREATE TABLE IF NOT EXISTS presenter_session_ends (
 session_id uuid PRIMARY KEY REFERENCES presenter_sessions(id),
 stream_url text NOT NULL, estimated_stopped_at timestamptz NOT NULL,
 confirmed_at timestamptz NOT NULL, reason text NOT NULL,
 observation_id uuid NOT NULL, details jsonb NOT NULL
);
"""


def iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def new_session(obs, detected_at, reason):
    return dict.fromkeys(COLUMNS) | {
        "id": str(uuid.uuid4()), "stream_url": obs["stream_url"],
        "detected_at": detected_at, "detection_reason": reason,
        "first_observation_id": obs["id"], "version_at": obs["observed_at"],
    }


def counter_sample(session, obs):
    value = obs.get("accepted_counter")
    if value is None:
        return
    previous = session["last_counter"]
    if previous is not None and value < previous:
        return
    if session["baseline_counter"] is None:
        session.update(baseline_counter=value, baseline_counter_at=obs["observed_at"])
    # Never infer a start from the global counter anchor of the previous person.
    if previous is not None and value > previous and session["folding_started_at"] is None:
        session.update(folding_started_at=obs["observed_at"], folding_start_counter=value,
                       folding_start_observation_id=obs["id"])
    if previous is not None and value > previous:
        session.update(last_increase_at=obs["observed_at"],
                       last_increase_lower_bound=session["last_counter_at"])
    session.update(last_counter=value, last_counter_at=obs["observed_at"])


def night_stop(state, obs, cfg):
    """End an anonymous stint on sustained night inactivity, requiring review.

    A stopped counter does not prove absence. Only fresh accepted readings
    count; gaps and unreadable readings interrupt the confirmation window.
    """
    current = state.get("current")
    if current is None:
        state.pop("night_activity", None)
        return None
    now = datetime.fromisoformat(obs["observed_at"])
    local = now.astimezone(cfg["timezone"]).time().replace(tzinfo=None)
    start, end = cfg["start"], cfg["end"]
    in_window = (start == end or start <= local < end) if start <= end else (local >= start or local < end)
    value = obs.get("accepted_counter")
    if not in_window or value is None:
        state.pop("night_activity", None)
        return None
    activity = state.get("night_activity")
    stamp = now.timestamp()
    if (not activity or activity["session_id"] != current["id"] or
            not 0 < stamp-activity["last_sample"] <= cfg["max_gap"] or
            activity["counter"] != value):
        increased = (value > activity["counter"] if activity else
                     current["last_counter"] is not None and value > current["last_counter"])
        # A final reading corrected during the break is not a newly observed
        # fold, and must not shift the previous presenter's last-increase time.
        if obs["details"].get("camera_view", {}).get("scene_kind") == "scheduled_break":
            increased = False
        trusted_increase = current.get("last_increase_at")
        if trusted_increase and current.get("last_counter_at") and trusted_increase > current["last_counter_at"]:
            trusted_increase = None
        last_increase = obs["observed_at"] if increased else trusted_increase
        lower_bound = (iso(activity["last_sample"]) if activity and increased else
                       current["last_counter_at"] if increased else current.get("last_increase_lower_bound"))
        break_scene = obs["details"].get("camera_view", {}).get("scene_kind") == "scheduled_break"
        fallback = current.get("last_counter_at") if break_scene else None
        estimate = last_increase or fallback or obs["observed_at"]
        basis = ("last_increase_observation" if last_increase else
                 "last_attributed_counter_observation" if fallback else "first_stationary_observation")
        activity = {"session_id": current["id"], "counter": value,
                    "stationary_since": stamp, "last_sample": stamp, "samples": 1,
                    "first_observation_id": obs["id"],
                    "estimated_stop_at": estimate, "estimated_stop_basis": basis,
                    "last_increase_at": last_increase,
                    "last_increase_lower_bound": lower_bound}
        state["night_activity"] = activity
        return None
    activity.update(last_sample=stamp, samples=activity["samples"]+1)
    if (obs["details"].get("camera_view", {}).get("scene_kind") == "scheduled_break" and
            current.get("last_counter_at") and activity.get("last_increase_at") and
            activity["last_increase_at"] > current["last_counter_at"]):
        activity.update(last_increase_at=None, estimated_stop_at=current["last_counter_at"],
                        estimated_stop_basis="last_attributed_counter_observation")
    if activity["samples"] < 3 or stamp-activity["stationary_since"] < cfg["seconds"]:
        return None
    estimated = max(current["detected_at"], activity["estimated_stop_at"])
    # If counter sampling establishes a last increase in this observation
    # sequence, its receipt time is a better upper-bound stop estimate.
    if activity["last_increase_at"] and activity["last_increase_at"] >= current["detected_at"]:
        estimated = activity["last_increase_at"]
    explicit_break = obs["details"].get("camera_view", {}).get("scene_kind") == "scheduled_break"
    reason = ("night_break_sign_counter_stopped_review_required" if explicit_break else
              "night_pause_counter_stopped_review_required")
    evidence = dict(activity, estimated_stop_at=estimated, confirmed_at=obs["observed_at"],
                    observation_id=obs["id"], reason=reason,
                    presenter_absence_verified=False,
                    break_scene_verified=explicit_break,
                    break_scene_evidence=obs["details"].get("camera_view", {}).get("break_scene"),
                    camera_view=obs["details"].get("camera_view", {}).get("view"))
    current.update(ended_at=estimated, end_reason=reason, end_evidence=evidence,
                   version_at=obs["observed_at"])
    if current["last_counter_at"] and current["last_counter_at"] > estimated:
        current["last_counter_at"] = estimated
    state["night_closed"] = {"session_id": current["id"], "counter": value,
                             "estimated_stop_at": estimated}
    for key in ("current", "candidate", "night_activity"):
        state.pop(key, None)
    return current


def advance_appearances(state, obs, manual_reset=False):
    """Preserve short clothing cues independently of sustained folding stints.

    An interrupted cue is evidence for review, never proof of a new identity
    or of zero folds. Unresolved transitions suspend attribution to the old stint.
    """
    state = json.loads(json.dumps(state))
    rows = []
    candidate = state.get("candidate")
    camera = obs["details"].get("camera_view", {})
    changed = obs.get("clothing_status") in (
        "change_pending", "possible_handover_review_required")
    since = camera.get("tracking", {}).get("changed_since")
    detected_at = iso(since) if since is not None else obs["observed_at"]
    if candidate and (manual_reset or not changed or candidate["detected_at"] != detected_at):
        candidate.update(status="interrupted_review_required" if camera.get("view") is None or camera.get("scene_kind") == "scheduled_break"
                         else "brief_appearance_review_required",
                         ended_at=obs["observed_at"], version_at=obs["observed_at"],
                         closing_observation_id=obs["id"], closing_frame_path=obs.get("frame_path"))
        rows.append(candidate)
        state.pop("candidate", None)
        state["attribution_review_required"] = True
        candidate = None
    if changed and not manual_reset:
        if candidate is None:
            candidate = new_session(obs, detected_at, "clothing_appearance_cue")
            candidate.update(first_frame_path=obs.get("frame_path"),
                             first_raw_counter=obs.get("raw_counter"))
        counter_sample(candidate, obs)
        candidate.update(version_at=obs["observed_at"],
                         last_observation_id=obs["id"], last_frame_path=obs.get("frame_path"),
                         last_raw_counter=obs.get("raw_counter"),
                         status="sustained_appearance_review_required" if
                         obs["clothing_status"] == "possible_handover_review_required" else "pending",
                         folding_evidence="accepted_increase_observed" if
                         candidate["folding_started_at"] else "no_accepted_increase_observed")
        rows.append(candidate)
        if obs["clothing_status"] == "possible_handover_review_required":
            state.pop("candidate", None)
            state.pop("attribution_review_required", None)
        else:
            state["candidate"] = candidate
    if manual_reset:
        state.pop("attribution_review_required", None)
    return state, rows


def advance_sessions(state, obs, manual_reset=False, night_config=None):
    """Return durable state and row snapshots to save with this observation.

    Pending candidates live only in monitor_state until the cue is sustained.
    Their counter samples are retained so starts before confirmation survive.
    """
    state = json.loads(json.dumps(state))
    updates = []
    if manual_reset:
        state.pop("night_closed", None)
        state.pop("night_activity", None)
    elif night_config:
        closed = night_stop(state, obs, night_config)
        if closed:
            return state, [closed], False
    resumed = state.get("night_closed")
    if resumed and not manual_reset:
        value = obs.get("accepted_counter")
        if value is None or value <= resumed["counter"]:
            return state, [], False
    status = obs.get("clothing_status")
    camera = obs["details"].get("camera_view", {})
    known = camera.get("view") is not None and camera.get("scene_kind") != "scheduled_break"
    current = state.get("current")
    candidate = state.get("candidate")
    if not known:
        state.pop("candidate", None)
        return state, updates, False
    changed = status in ("change_pending", "possible_handover_review_required")
    promote = False
    if current is None or manual_reset:
        if current:
            current.update(ended_at=obs["observed_at"], end_reason="manual_baseline_reset",
                           version_at=obs["observed_at"])
            updates.append(current)
        current = new_session(obs, obs["observed_at"],
                              "manual_baseline_reset" if manual_reset else
                              "activity_resumed_after_night_pause" if resumed else "first_observed")
        state.pop("night_closed", None)
        promote = bool(resumed)
        current["confirmed_at"] = obs["observed_at"]
        state.pop("candidate", None)
        counter_sample(current, obs)
    elif changed:
        since = obs["details"]["camera_view"]["tracking"]["changed_since"]
        detected_at = iso(since)
        if candidate is None or candidate["detected_at"] != detected_at:
            candidate = new_session(obs, detected_at, "sustained_clothing_change")
        counter_sample(candidate, obs)
        state["candidate"] = candidate
        # At night an empty chair can look like a sustained shirt change.
        # Preserve that appearance cue, but require an increase within the
        # candidate before promoting it to a new folding stint.
        idle_night_candidate = bool(night_config and state.get("night_activity")
                                    and candidate["folding_started_at"] is None)
        if status == "possible_handover_review_required" and not idle_night_candidate:
            current.update(ended_at=detected_at, end_reason="sustained_clothing_change",
                           version_at=obs["observed_at"])
            updates.append(current)
            candidate.update(confirmed_at=obs["observed_at"])
            current = candidate
            state.pop("candidate", None)
            promote = True
        else:
            # Do not attribute a potential new presenter's folds to the old stint.
            state["current"] = current
            return state, updates, False
    else:
        state.pop("candidate", None)
        if not obs["details"].get("presenter_attribution_review_required"):
            counter_sample(current, obs)
    current["version_at"] = obs["observed_at"]
    state["current"] = current
    updates.append(current)
    return state, updates, promote


def save_sessions(conn, obs):
    """Monotonic snapshot upserts make outbox replay safe and idempotent."""
    for session in obs["details"].get("presenter_session_updates", []):
        assignments = ",".join(f"{name}=EXCLUDED.{name}" for name in COLUMNS if name != "id")
        conn.execute("INSERT INTO presenter_sessions (" + ",".join(COLUMNS) + ") VALUES ("
                     + ",".join(["%s"] * len(COLUMNS)) + ") ON CONFLICT (id) DO UPDATE SET "
                     + assignments + " WHERE presenter_sessions.version_at < EXCLUDED.version_at",
                     tuple(session[name] for name in COLUMNS))
        if session.get("end_evidence"):
            from psycopg.types.json import Jsonb
            evidence = session["end_evidence"]
            conn.execute("INSERT INTO presenter_session_ends "
                         "(session_id,stream_url,estimated_stopped_at,confirmed_at,reason,observation_id,details) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id) DO UPDATE SET "
                         "estimated_stopped_at=EXCLUDED.estimated_stopped_at,confirmed_at=EXCLUDED.confirmed_at,"
                         "reason=EXCLUDED.reason,observation_id=EXCLUDED.observation_id,details=EXCLUDED.details "
                         "WHERE presenter_session_ends.confirmed_at < EXCLUDED.confirmed_at",
                         (session["id"], session["stream_url"], session["ended_at"], evidence["confirmed_at"],
                          session["end_reason"], evidence["observation_id"], Jsonb(evidence)))
    from psycopg.types.json import Jsonb
    for appearance in obs["details"].get("presenter_appearance_updates", []):
        conn.execute("INSERT INTO presenter_appearances "
                     "(id,stream_url,detected_at,version_at,status,details) VALUES (%s,%s,%s,%s,%s,%s) "
                     "ON CONFLICT (id) DO UPDATE SET version_at=EXCLUDED.version_at, "
                     "status=EXCLUDED.status,details=EXCLUDED.details "
                     "WHERE presenter_appearances.version_at < EXCLUDED.version_at",
                     (appearance["id"], appearance["stream_url"], appearance["detected_at"],
                      appearance["version_at"], appearance["status"], Jsonb(appearance)))
