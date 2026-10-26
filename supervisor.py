"""Periodic live-frame analysis with durable PostgreSQL observations and state."""

import argparse
from datetime import datetime, time as wall_time, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent / "proof-of-concept"))
from read_counter import recognize
from camera_views import observe, detect_break_scene
import polling
import frame_retention
import clothing_descriptions
import presenter_video_events
from presenter_sessions import SCHEMA as SESSION_SCHEMA, advance_sessions, advance_appearances, save_sessions


SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
 id uuid PRIMARY KEY, stream_url text NOT NULL, observed_at timestamptz NOT NULL,
 status text NOT NULL, raw_counter bigint, accepted_counter bigint,
 counter_status text, counter_reason text, rate double precision,
 clothing_status text, clothing_score double precision,
 frame_path text, details jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS observations_stream_time ON observations(stream_url, observed_at DESC);
CREATE TABLE IF NOT EXISTS monitor_state (
 stream_url text PRIMARY KEY, updated_at timestamptz NOT NULL DEFAULT now(),
 state jsonb NOT NULL
);
""" + SESSION_SCHEMA + polling.SCHEMA + clothing_descriptions.SCHEMA + presenter_video_events.SCHEMA


def number(name, default, minimum=0):
    value = float(os.getenv(name, default))
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"Invalid {name}")
    return value


def config():
    roi = tuple(float(n) for n in os.getenv("CLOTHING_ROI", ".48,.51,.65,.62").split(","))
    if len(roi) != 4 or not (0 <= roi[0] < roi[2] <= 1 and 0 <= roi[1] < roi[3] <= 1):
        raise ValueError("Invalid CLOTHING_ROI")
    cfg = {
        "stream": os.getenv("STREAM_URL", "https://www.youtube.com/watch?v=vaiHWiExDXg"),
        "interval": number("INTERVAL_SECONDS", 4, 1),
        "poll_max": number("BACKPRESSURE_MAX_INTERVAL_SECONDS", 60, 1),
        "poll_stable": number("BACKPRESSURE_STABLE_SAMPLES", 12, 1),
        "expected_min": number("EXPECTED_RATE_MIN", 29.5/60, .001),
        "expected_max": number("EXPECTED_RATE_MAX", 56.7/60, .001),
        "max_rate": number("MAX_RATE", 5, .001),
        "tolerance": number("RATE_TOLERANCE", .5),
        "slack": number("COUNTER_SLACK", 5),
        "rebase": number("REBASE_AFTER_SECONDS", 600, 1),
        "roi": roi, "clothing_threshold": number("CLOTHING_THRESHOLD", .45, .001),
        "confirm": number("HANDOVER_CONFIRM_SECONDS", 60, 1),
        "night_idle": number("NIGHT_STOP_IDLE_SECONDS", 180, 1),
        "night_start": wall_time.fromisoformat(os.getenv("NIGHT_STOP_START", "21:30")),
        "night_end": wall_time.fromisoformat(os.getenv("NIGHT_STOP_END", "06:30")),
        "timezone": ZoneInfo(os.getenv("TIMEZONE", "Europe/Warsaw")),
        "start": wall_time.fromisoformat(os.getenv("ACTIVE_START", "00:00")),
        "end": wall_time.fromisoformat(os.getenv("ACTIVE_END", "00:00")),
        "image_max_bytes": int(number("IMAGE_STORAGE_LIMIT_BYTES", 20_000_000_000, 1)),
        "data": Path(os.getenv("DATA_DIR", "data")),
    }
    if cfg["interval"]>cfg["poll_max"] or not cfg["poll_stable"].is_integer():
        raise ValueError("Backpressure maximum must cover INTERVAL_SECONDS; stable samples must be an integer")
    if not cfg["expected_min"] <= cfg["expected_max"] <= cfg["max_rate"]:
        raise ValueError("Expected rates must satisfy 0 < EXPECTED_RATE_MIN <= EXPECTED_RATE_MAX <= MAX_RATE")
    if cfg["clothing_threshold"] > 1:
        raise ValueError("Clothing threshold exceeds 1")
    return cfg


def active(now, cfg):
    current = now.astimezone(cfg["timezone"]).time().replace(tzinfo=None)
    start, end = cfg["start"], cfg["end"]
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def pace_bounds(elapsed, rates, cfg):
    """Use the published range initially; tolerance applies to learned pace only."""
    expected = statistics.median(rates) if rates else None
    low, high = ((expected*(1-cfg["tolerance"]), expected*(1+cfg["tolerance"]))
                 if expected is not None else (cfg["expected_min"], cfg["expected_max"]))
    return {"expected_rate": expected, "expected_rate_range": [low, high],
            "rate_model": "recent_median" if rates else "published_initial_range",
            "allowed_delta": [max(0, low*elapsed-cfg["slack"]),
                              min(cfg["max_rate"]*elapsed+cfg["slack"], high*elapsed+cfg["slack"])],
            "elapsed_seconds": elapsed}


def validate_counter(value, ts, state, cfg, break_confirmed=False, resuming_after_pause=False):
    """Return verdict and next state; rejected samples never move the anchor."""
    result = {"raw": value, "accepted": None, "rate": None}
    state = json.loads(json.dumps(state))
    if value is None:
        return dict(result, status="unreadable", reason="Unknown digit pattern"), state
    anchor = state.get("anchor")
    if anchor is not None:
        elapsed = ts-anchor["time"]
        delta = value-anchor["value"]
        if elapsed <= 0:
            return dict(result, status="rejected", reason="Non-increasing timestamp"), state
        if delta < 0:
            return dict(result, status="rejected", reason="Counter decreased", rate=delta/elapsed), state
        if delta > cfg["max_rate"]*elapsed+cfg["slack"]:
            return dict(result, status="rejected", reason="Increase exceeds absolute maximum pace",
                        rate=delta/elapsed), state
        if break_confirmed and 0 < delta <= cfg["slack"]:
            state.update(anchor={"value": value, "time": ts}, pending=None)
            return dict(result, accepted=value, status="accepted_break_boundary",
                        reason="Repeated calibrated break-scene reading confirms a small final increment; attribution requires review",
                        rate=delta/elapsed), state
    if anchor is None or ts-anchor["time"] > cfg["rebase"] or resuming_after_pause:
        pending = state.get("pending")
        if pending and 0 < ts-pending["time"] <= cfg["rebase"]:
            dt = ts-pending["time"]
            delta = value-pending["value"]
            if 0 <= delta <= cfg["max_rate"]*dt+cfg["slack"]:
                result.update(pace_bounds(dt, [], cfg))
                lower, upper = result["allowed_delta"]
                if delta > upper:
                    return dict(result, status="rejected", reason="Increase exceeds initial pace range",
                                rate=delta/dt), state
                if 0 < delta < lower:
                    return dict(result, status="suspect_slow", reason="Increase below initial pace range",
                                rate=delta/dt), state
                state.update(anchor={"value": value, "time": ts}, rates=[], pending=None)
                return dict(result, accepted=value, status="accepted_rebased",
                            reason="Two plausible readings establish an initial or post-gap anchor",
                            rate=delta/dt), state
        state["pending"] = {"value": value, "time": ts}
        return dict(result, status="provisional", reason="Waiting for a second plausible reading"), state
    dt = ts-anchor["time"]
    delta = value-anchor["value"]
    rate = delta/dt
    result.update(pace_bounds(dt, state.get("rates", []), cfg))
    lower, upper = result["allowed_delta"]
    if delta > upper:
        return dict(result, status="rejected", reason="Increase exceeds plausible pace", rate=rate), state
    if 0 < delta < lower:
        return dict(result, status="suspect_slow", reason="Increase below recent pace", rate=rate), state
    state["anchor"] = {"value": value, "time": ts}
    if delta:
        state["rates"] = (state.get("rates", []) + [rate])[-20:]
    return dict(result, accepted=value, status="accepted_paused" if not delta else "accepted",
                reason="Unchanged counter; pause or stalled video possible" if not delta
                else "Monotonic and within pace bounds", rate=rate), state


def capture(url, destination):
    try:
        meta = subprocess.run(
            ["yt-dlp", "--skip-download", "--dump-single-json", "--no-playlist",
             "--socket-timeout", "15", "--retries", "1", url],
            capture_output=True, text=True, timeout=50, check=True)
        info = json.loads(meta.stdout)
        if not info.get("is_live"):
            raise RuntimeError("Stream is not live")
        formats = [f for f in info["formats"] if f.get("height")
                   and f.get("height") <= 1080 and f.get("url")
                   and f.get("protocol", "").startswith("m3u8")]
        if not formats:
            raise RuntimeError("No usable live HLS format")
        selected = max(formats, key=lambda f: f["height"])
        subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
             "-rw_timeout", "20000000", "-live_start_index", "-1", "-i", selected["url"],
             "-frames:v", "1", "-q:v", "2", "-update", "1", "-y", str(destination)],
            capture_output=True, timeout=40, check=True)
        return {"title": info.get("title"), "height": selected["height"],
                "video_timing": presenter_video_events.capture_metadata(info)}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Signed media URLs and command lines should not leak into logs.
        raise RuntimeError(f"Stream capture failed: {type(exc).__name__}") from None


def observation(cfg):
    return {"id": str(uuid.uuid4()), "stream_url": cfg["stream"],
            "observed_at": datetime.now(timezone.utc).isoformat(), "status": "error",
            "details": {"polling_interval_seconds": cfg.get("effective_interval",cfg["interval"])}}


def analyze(cfg, state, obs, fixture=None, reset_clothing=False):
    folder = cfg["data"] / "frames" / obs["id"]
    folder.mkdir(parents=True)
    path = folder / "frame.jpg"
    if fixture:
        shutil.copyfile(fixture, path)
        obs["details"]["fixture"] = True
    else:
        obs["details"]["capture"] = capture(cfg["stream"], path)
    # This is local receipt time, not the broadcaster's source timestamp.
    now = datetime.now(timezone.utc)
    obs["observed_at"] = now.isoformat()
    obs["frame_path"] = str(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest == state.get("last_frame_hash"):
        obs.update(status="duplicate_frame", counter_status="not_analyzed",
                   clothing_status="not_analyzed")
        return state
    state["last_frame_hash"] = digest
    image = Image.open(path).convert("RGB")
    is_break, break_signal = detect_break_scene(image)
    obs["details"]["break_scene"] = break_signal
    try:
        raw, _ = recognize(image)
        obs["details"]["ocr"] = raw
        draw = ImageDraw.Draw(image)
        for digit in raw["digits"]:
            draw.rectangle(digit["bbox"], outline="lime", width=2)
        image.save(folder / "annotated.jpg")
    except ValueError as exc:
        raw = {"value": None}
        obs["details"]["ocr_error"] = str(exc)
    confirmation = state.get("break_counter_confirmation", {})
    interval = cfg.get("effective_interval", cfg["interval"])
    if is_break and raw["value"] is not None:
        continuous = (confirmation.get("counter") == raw["value"] and
                      0 < now.timestamp()-confirmation.get("last_sample",0) <= max(2.5*interval,25))
        confirmation = {"counter": raw["value"], "last_sample": now.timestamp(),
                        "samples": confirmation["samples"]+1 if continuous else 1}
        state["break_counter_confirmation"] = confirmation
    else:
        state.pop("break_counter_confirmation", None)
        confirmation = {}
    obs["details"]["break_counter_confirmation"] = confirmation
    closed = state.get("presenters", {}).get("night_closed")
    resuming = bool(closed and not is_break and raw["value"] is not None
                    and raw["value"] > closed["counter"])
    verdict, counter_state = validate_counter(raw["value"], now.timestamp(), state.get("counter", {}), cfg,
                                             break_confirmed=confirmation.get("samples",0)>=3,
                                             resuming_after_pause=resuming)
    state["counter"] = counter_state
    obs["details"]["counter_validation"] = verdict
    obs.update(raw_counter=verdict["raw"], accepted_counter=verdict["accepted"],
               counter_status=verdict["status"], counter_reason=verdict["reason"], rate=verdict["rate"])
    # Match the broadcast layout before selecting a clothing-only crop.
    clothing, status, score, crop, details = observe(
        Image.open(path), state.get("clothing", {}), now.timestamp(), obs["id"],
        cfg["roi"], cfg["clothing_threshold"], cfg["confirm"], cfg.get("effective_interval",cfg["interval"]),
        reset=reset_clothing, evidence_root=cfg["data"] / "frames")
    if crop is not None:
        crop.save(folder / "clothing.png")
    state["clothing"] = clothing
    obs.update(clothing_status=status, clothing_score=score)
    obs["details"]["camera_view"] = details
    obs["details"]["clothing_baseline_observation"] = details.get("baseline_observation")
    obs["status"] = "analyzed"
    pause = state.get('presenters', {}).get('activity_pause')
    if pause and not reset_clothing:
        # Retain camera/OCR evidence without inventing clothing transitions
        # during a human-confirmed non-folding test.
        appearances, appearance_updates = state.get('appearances', {}), []
        obs['details']['folding_pause'] = pause
    else:
        appearances, appearance_updates = advance_appearances(state.get("appearances", {}), obs,
                                                             manual_reset=reset_clothing)
    state["appearances"] = appearances
    obs["details"]["presenter_appearance_updates"] = appearance_updates
    obs["details"]["presenter_attribution_review_required"] = bool(
        appearances.get("attribution_review_required") or details.get('baseline_review_required'))
    sessions, updates, promote = advance_sessions(state.get("presenters", {}), obs,
        manual_reset=reset_clothing,
        night_config={"timezone": cfg["timezone"], "start": cfg["night_start"],
                      "end": cfg["night_end"], "seconds": cfg["night_idle"],
                      "max_gap": max(2.5*cfg.get("effective_interval",cfg["interval"]), 25)})
    state["presenters"] = sessions
    obs["details"]["presenter_session_updates"] = updates
    obs["details"]["presenter_session_id"] = sessions.get("current", {}).get("id")
    obs["details"]["presenter_candidate_id"] = sessions.get("candidate", {}).get("id")
    if promote:
        if sessions["current"]["detection_reason"] in ("activity_resumed_after_night_pause", "activity_resumed_after_confirmed_pause"):
            # Yesterday's interrupted appearance belongs to the closed stint.
            # A known view plus accepted resumed activity establishes a new
            # anonymous stint; stale review state must not freeze its samples.
            candidate = appearances.pop("candidate", None)
            if candidate:
                candidate.update(status="resolved_on_activity_resumption_review_required",
                                 ended_at=obs["observed_at"], version_at=obs["observed_at"],
                                 closing_observation_id=obs["id"],
                                 closing_frame_path=obs.get("frame_path"))
                appearance_updates = [row for row in appearance_updates if row["id"] != candidate["id"]]
                appearance_updates.append(candidate)
                obs["details"]["presenter_appearance_updates"] = appearance_updates
            appearances.pop("attribution_review_required", None)
            obs["details"]["presenter_attribution_review_required"] = False
            obs["details"]["presenter_appearance_resolution"] = ('new_stint_after_night_pause'
                if sessions['current']['detection_reason'] == 'activity_resumed_after_night_pause'
                else 'new_stint_after_confirmed_pause')
            obs['details'].pop('folding_pause', None)
        # Acknowledge this cue once, then compare against the new presenter's shirt.
        # Other views must be relearned rather than retaining the previous shirt.
        state["clothing"], _, _, _, _ = observe(
            Image.open(path), {}, now.timestamp(), obs["id"], cfg["roi"],
            cfg["clothing_threshold"], cfg["confirm"], cfg.get("effective_interval",cfg["interval"]), reset=True)
        obs["details"]["camera_view"]["baseline_replaced_for_session"] = True
    with Image.open(path) as clothing_image:
        clothing_descriptions.prepare(clothing_image, obs, sessions)
    state["last_observed_at"] = obs["observed_at"]
    if status == "camera_view_unknown_review_required":
        # Preserve new-angle evidence outside the rolling frame retention tree.
        archive = cfg["data"] / "camera-review" / obs["id"]
        shutil.copytree(folder, archive)
        obs["details"]["camera_review_path"] = str(archive)
        (archive / "observation.json").write_text(json.dumps(obs, indent=2) + "\n")
    if appearance_updates:
        archive = cfg["data"] / "presenter-review" / obs["id"]
        shutil.copytree(folder, archive)
        obs["details"]["presenter_review_path"] = str(archive)
        (archive / "observation.json").write_text(json.dumps(obs, indent=2) + "\n")
    return state


def insert(conn, obs):
    from psycopg.types.json import Jsonb
    columns = ("id", "stream_url", "observed_at", "status", "raw_counter", "accepted_counter",
               "counter_status", "counter_reason", "rate", "clothing_status", "clothing_score", "frame_path")
    conn.execute("INSERT INTO observations (" + ",".join(columns) + ",details) VALUES ("
                 + ",".join(["%s"]*13) + ") ON CONFLICT (id) DO NOTHING",
                 tuple(obs.get(c) for c in columns) + (Jsonb(clothing_descriptions.public_details(obs)),))
    save_sessions(conn, obs)
    presenter_video_events.save(conn, obs)
    clothing_descriptions.save_job(conn, obs)


_READY_SCHEMAS = set()


def ensure_schema(conn):
    # Never hold CREATE OR REPLACE VIEW locks during network capture or inserts.
    parameters = conn.info.get_parameters()
    key = tuple(parameters.get(name) for name in ('host','port','dbname','user','options'))
    if key not in _READY_SCHEMAS:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('folding-schema-setup'))")
        conn.execute(SCHEMA)
        conn.commit()
        _READY_SCHEMAS.add(key)


def cycle(cfg, fixture=None, reset_clothing=False):
    import psycopg
    from psycopg.types.json import Jsonb
    obs = observation(cfg)
    outbox = cfg["data"] / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    flushed = []
    queued_state = None
    try:
        with psycopg.connect(os.getenv("DATABASE_URL", ""), connect_timeout=10) as conn:
            ensure_schema(conn)
            # Serialize a stream, including across overlapping supervisor processes.
            locked = conn.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s))", (cfg["stream"],)).fetchone()[0]
            if not locked:
                print(json.dumps({"status": "skipped_already_running"}), flush=True)
                return {"status": "skipped_already_running"}
            row = conn.execute("SELECT state FROM monitor_state WHERE stream_url=%s", (cfg["stream"],)).fetchone()
            state = row[0] if row else {}
            for pending in sorted(outbox.glob("*.json")):
                saved_obs = json.loads(pending.read_text())
                insert(conn, saved_obs)
                recovered = saved_obs.get("_monitor_state")
                if (saved_obs["stream_url"] == cfg["stream"] and recovered
                        and recovered.get("last_observed_at", "") > state.get("last_observed_at", "")):
                    state = recovered
                flushed.append(pending)
            if active(datetime.now(timezone.utc), cfg):
                try:
                    state = analyze(cfg, json.loads(json.dumps(state)), obs, fixture, reset_clothing)
                except Exception as exc:
                    obs["details"]["error"] = f"{type(exc).__name__}: {exc}"
            else:
                obs["status"] = "outside_active_hours"
            if obs["status"] == "analyzed":
                queued_state = state
            frame_retention.register(cfg["data"], obs)
            insert(conn, obs)
            conn.execute("INSERT INTO monitor_state (stream_url,state) VALUES (%s,%s) "
                         "ON CONFLICT (stream_url) DO UPDATE SET state=EXCLUDED.state, updated_at=now()",
                         (cfg["stream"], Jsonb(state)))
        for pending in flushed:
            pending.unlink(missing_ok=True)
    except psycopg.Error:
        # No analysis is started without a database connection. On a mid-cycle
        # outage queue the completed observation and model together for replay.
        obs["details"]["database_error"] = "Database unavailable; observation queued locally"
        if "frame_path" not in obs:
            obs["status"] = "database_unavailable"
        pending = outbox / (obs["id"] + ".json")
        temp = pending.with_suffix(".tmp")
        payload = dict(obs)
        if queued_state is not None:
            payload["_monitor_state"] = queued_state
        temp.write_text(json.dumps(payload))
        temp.replace(pending)
        obs["status"] = "database_unavailable"
        frame_retention.register(cfg["data"], obs)
    print(json.dumps(dict(obs, details=clothing_descriptions.public_details(obs))), flush=True)
    return obs


def prune(cfg):
    try:
        return frame_retention.prune(cfg["data"])
    finally:
        frame_retention.enforce_image_limit(cfg["data"], cfg.get("image_max_bytes", 20_000_000_000))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--fixture", type=Path, help="Analyze a saved image instead of the stream; requires --once")
    parser.add_argument("--reset-clothing-baseline", action="store_true")
    args = parser.parse_args()
    if args.fixture and not args.once:
        parser.error("Fixture mode requires --once; use a separate STREAM_URL for test data")
    cfg = config()
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    try:
        print(json.dumps({"frame_retention_migration": frame_retention.migrate(cfg["data"])}), flush=True)
    except Exception as exc:
        # Unclassified historical folders stay untouched until DB recovery/restart.
        print(json.dumps({"frame_retention_migration_error": type(exc).__name__}), flush=True)
    frame_retention.enforce_image_limit(cfg["data"], cfg["image_max_bytes"])
    pressure = polling.load(cfg)
    while not stopped.is_set():
        started = time.monotonic()
        # Import prior timing evidence inside the measured work budget.
        telemetry = polling.flush(cfg)
        cfg["effective_interval"] = min(cfg["poll_max"], max(cfg["interval"],
            pressure.get("effective_interval_seconds",cfg["interval"])))
        result = cycle(cfg, args.fixture, args.reset_clothing_baseline)
        if result.get("clothing_status") == "baseline_created":
            args.reset_clothing_baseline = False
        prune(cfg)
        if args.once:
            return 1 if result["status"] in ("error", "database_unavailable") else 0
        elapsed = time.monotonic()-started
        pressure = polling.advance(pressure, elapsed, cfg["interval"], cfg["poll_max"],
                                   int(cfg["poll_stable"]), result["status"],
                                   appearance_pending=result.get('clothing_status') == 'change_pending')
        saved = polling.record(cfg,pressure,result.get("id"),import_events=False)
        print(json.dumps({"polling":pressure,"prior_telemetry_queued":telemetry['queued']}),flush=True)
        # Scheduling is relative to this cycle, with no backlog or catch-up loop.
        delay = polling.wait_seconds(pressure,time.monotonic()-started)
        stopped.wait(delay)
    polling.flush(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
