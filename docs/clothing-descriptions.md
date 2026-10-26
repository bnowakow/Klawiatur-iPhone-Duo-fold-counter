# Clothing descriptions for folding stints

Each confirmed anonymous presenter stint gets one short Polish clothing
description after its first accepted counter increase. For example: “Biała koszulka z krótkim rękawem.” The local vision model describes visible garment colors
and types; items outside the crop cannot be established. These descriptions are model estimates,
not verified identity labels or additional person-detection evidence. The saved
short text includes only recognized garment phrases; accessories and facial
details occasionally added by the model are excluded. These torso crops support
upper-body garments; model claims about hidden trousers or other lower-body
clothing are excluded. The raw model response
remains available in `response.raw_description` for auditing. If no garment phrase
can be extracted, the result is marked uncertain.

The supervisor selects the torso in the calibrated `presenter_main` or
`presenter_inset` view. Main-view crops mask the table-camera inset. Descriptions
wait while attribution is uncertain, another presenter candidate is pending, the
camera is unknown, or the scheduled break scene is visible. An already active
stint can receive a description on its next eligible frame after deployment.
Closed historical stints are not automatically described using a different
person's current image.

The crop and job are stored transactionally alongside session updates in
PostgreSQL. A separate `clothing-descriptions` worker calls the local `vision`
service, keeping model inference outside the capture loop. Each session UUID is
the queue's primary key: repeated samples and outbox replay do not create duplicate
jobs or replace completed descriptions. The worker processes only its configured
`STREAM_URL`, keeping isolated integration-test streams out of live inference. Failures remain visible in logs and retry
after five minutes. “Clothing not clearly visible” results have status `uncertain`
instead of being treated as verified descriptions. No remote vision API is used.

## Database contract

`presenter_clothing_descriptions` stores:

- `session_id`, `observation_id`, `observed_at`: stint and source-frame provenance.
- `camera_view`, `crop_roi`, `image_sha256`, `image`: the actual JPEG input crop.
- `description`, `description_language`, `status`: Polish text (`description_language=pl`) and `pending`, `retry`, `completed`, or
  `uncertain` status.
- `model`, `prompt_version`, `generated_at`, `response`: model and inference metadata.
- `attempts`, `retry_at`, `last_error`: retry diagnostics.

The observation's `details.clothing_description_job` records provenance without
duplicating the encoded crop. During a database outage its replay payload retains
the crop, so generation can recover without relying on an image file that ordinary
retention may remove. The source crop remains a small PostgreSQL bytea value per
stint; it is outside the image-file cap, just like other database contents.

```sql
SELECT s.id, s.detected_at, s.folding_started_at,
       d.description, d.description_language, d.status, d.observed_at, d.generated_at, d.model
FROM presenter_sessions s
LEFT JOIN presenter_clothing_descriptions d ON d.session_id = s.id
ORDER BY s.detected_at DESC;
```

## Deployment and resources

`docker compose up -d` starts the local Ollama server and the one-shot
`vision-model` downloader, then starts the description worker after model setup.
The default `CLOTHING_DESCRIPTION_MODEL=moondream:1.8b` downloads about 1.7 GB of
weights into the persistent `vision_models` Docker volume. The Ollama container
image consumes additional disk space. These runtime/model files are outside the
20 GB image-file cap. Input crops are limited to 384 pixels on the longest side. The model is unloaded after each caption; the server is
limited to two CPUs and 4 GB RAM. Initial setup requires network access, while
subsequent inference runs locally. Failed setup retries; ordinary frame capture
continues independently.

`make docker-upgrade` builds and replaces the description worker along with the
supervisor and website collector. Its dependencies ensure the model is available.
Back up PostgreSQL for description history; model weights can be downloaded again.

Sources: [Moondream model](https://ollama.com/library/moondream),
[Ollama generate API](https://github.com/ollama/ollama/blob/main/docs/api.md).

## Polish output

The local vision model keeps its validated English prompt. Garment phrases are
converted deterministically to Polish with matching adjective gender, color shades,
sleeve lengths and visible patterns. For example, “Black shirt.” becomes
“Czarna koszula.” and “Grey sweater.” becomes “Szary sweter.” Descriptions with no
clear clothing become “Ubiór nie jest wyraźnie widoczny.” with status `uncertain`.

The worker converts existing English descriptions on startup without rerunning
vision inference. The original caption and previous text remain in response
metadata for provenance. Conversion is idempotent and stored descriptions have
`description_language=pl`. Raw model diagnostics remain in their original language.
