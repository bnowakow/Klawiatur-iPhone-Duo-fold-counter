# Video review timestamps

The supervisor stores three bookmarks per anonymous folding stint in
`presenter_video_events`:

- `appearance_start`: the first sample of the detected appearance.
- `folding_start`: the first accepted counter increase within that stint.
- `folding_finish`: the estimated endpoint when the stint closes, including
  a sustained clothing change, night stop or reviewed correction.

These are detector predictions. The first observed increase can follow the
physical start, and a handover confirmation can follow the physical finish.
Events retain UTC timestamps, source and confirmation observation UUIDs, source
frame paths, the preceding sample/counter, end evidence and detection reason.
Starts and finish confirmations protect the normal ten-frame before/after
retention windows. The global image-storage cap still applies; timestamps and
database evidence do not expire when images are evicted.

## Video positions and links

Each live capture retains the YouTube video ID and broadcast start returned by
yt-dlp's `release_timestamp` (YouTube's live `startTimestamp`). The video offset
is the event's capture-clock timestamp minus that broadcast start. Its quality
is explicitly `estimated_from_capture_clock`: capture delay and broadcaster/
player latency are unknown. It is not a decoded frame PTS or an exact physical
start/stop. No upload date or hourly schedule is substituted for a missing
broadcast start. Missing or inconsistent timing leaves a null offset/link and
`timing_quality=unavailable`.

`review_url` seeks 30 seconds before the estimated event offset, clamped to zero,
to provide context. This lead-in is a convenience, not a bound on live latency.
YouTube DVR/archive availability and any later archive trimming are outside the
application's control. Video URLs are bookmarks; this does not download clips.
Only the video ID/start metadata is retained, never signed media URLs.

```sh
make docker-video-events
```

This prints the latest 30 bookmarks as JSON, including video offsets in seconds,
clickable review URLs, evidence and review fields. To export more:

```sh
docker compose exec -T supervisor python presenter_video_events.py --limit 1000 > video-events.json
```

Existing stints can be indexed without changing counters or attribution:

```sh
docker compose exec -T supervisor python presenter_video_events.py --backfill
```

Legacy observations without video metadata can reuse a later captured broadcast
anchor for the same stream. This remains a capture-clock estimate; the anchor
observation ID is retained. Events preceding that broadcast start remain
unavailable. Restart/outage replay upserts the same `(session_id,event_kind)`
instead of duplicating it.

## Review before training

Every event starts with `review_status=unreviewed`. It is not ground truth.
Reviewers can retain a confirmed, corrected or rejected label separately in
`review_status`, `reviewed_video_offset_seconds`, `reviewed_at`, `reviewer` and
`review_notes`. Automatic replay/backfill preserves these fields. Keep reviewed
offsets separate from the original estimated `video_offset_seconds`. The
`details.training_label_verified=false` flag describes the automatic prediction;
review columns are the separate human annotation. A training exporter must
explicitly select human-reviewed labels and account for uncertainty, rather
than treating all inferred transitions as correct examples.
