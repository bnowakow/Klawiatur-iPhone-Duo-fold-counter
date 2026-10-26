# Clothing descriptions for folding stints

Each confirmed anonymous presenter stint gets a Polish appearance
description after its first accepted counter increase. For example: “Biała koszulka z krótkim rękawem.” The local vision model describes visible garment colors
and types; items outside the crop cannot be established. These descriptions are model estimates,
not verified identity labels or additional person-detection evidence. The clothing section includes recognized garment phrases. Hair, headwear, glasses,
facial hair and accessories are collected in separate structured fields. These torso crops support
upper-body garments; model claims about hidden trousers or other lower-body
clothing are excluded. The raw model response
remains available in `response.raw_answers` (or `response.raw_description` for older clothing-only records) for auditing. If no garment phrase
can be extracted, the result is marked uncertain.

The supervisor selects the visible head and upper body in the calibrated `presenter_main` or
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
The default `CLOTHING_DESCRIPTION_MODEL=llava-phi3:3.8b` downloads about 2.9 GB of
weights into the persistent `vision_models` Docker volume. The Ollama container
image consumes additional disk space. These runtime/model files are outside the
20 GB image-file cap. Input crops are limited to 384 pixels on the longest side. The model is unloaded after each caption; the server is
limited to two CPUs and 6 GB RAM. Initial setup requires network access, while
subsequent inference runs locally. Failed setup retries; ordinary frame capture
continues independently.

`make docker-upgrade` builds and replaces the description worker along with the
supervisor and website collector. Its dependencies ensure the model is available.
Back up PostgreSQL for description history; model weights can be downloaded again.

Sources: [LLaVA-Phi3 model](https://ollama.com/library/llava-phi3),
[Ollama generate API](https://github.com/ollama/ollama/blob/main/docs/api.md).

## Polish output

The local vision model returns structured English observations. Garment phrases are
converted deterministically to Polish with matching adjective gender, color shades,
sleeve lengths and visible patterns. For example, “Black shirt.” becomes
“Czarna koszula.” and “Grey sweater.” becomes “Szary sweter.” Descriptions with no
clear clothing become “Ubiór nie jest wyraźnie widoczny.” with status `uncertain`.

The worker converts existing English descriptions on startup without rerunning
vision inference. The original caption and previous text remain in response
metadata for provenance. Conversion is idempotent and stored descriptions have
`description_language=pl`. Raw model diagnostics remain in their original language.

## Detailed appearance descriptors (version 2)

Descriptions now include the visible head and upper body: clothing, hair color,
length and style, headwear, eyeglasses, facial hair, and visible accessories.
The Polish sentence is stored in `description`; machine-readable attributes are
stored in `appearance` JSONB. Each attribute has a `state`: `visible`,
`not_visible`, or `unknown`. “Not visible” is limited to this image and does not
prove that an item is absent. Unclear views are recorded as “nie można ocenić”.
Negative glasses answers are phrased as “Okulary: brak widocznych okularów.”
Existing ambiguous “Okulary: niewidoczne.” descriptions are migrated on worker
startup, preserving the original row/image in `presenter_description_revisions`
and retaining the model's raw answer and structured state.
There is no inferred sex or gender-identity field. Unsolicited gender labels from
the model are neutralized in diagnostic answers. The descriptors help compare
anonymous stints; they do not establish that two stints belong to the same person.

The local LLaVA-Phi3 model answers one structured JSON request covering all six
appearance categories. The input fits within 384 pixels on the longest side.
The model unloads after each descriptor. The saved main-camera fixture took about two minutes on this laptop with the two-CPU
limit. The previous smaller model returned
empty answers for several appearance questions, so it is replaced for v2.
Incomplete or malformed responses retry rather than fabricating attributes.

`input_version=2` marks a head-and-upper-body crop. `prompt_version=2` marks the
completed detailed descriptor. When upgrading a source image, the previous row
snapshot and crop are archived in `presenter_description_revisions`. Repeated or
older queued observations cannot overwrite a newer input. A completed descriptor
remains visible with `pending` status while its richer replacement is generated.

On startup the worker refreshes older inputs only from the original observation's
full frame, if it remains in `data/frames`, `data/camera-review`, or
`data/presenter-review`. Its data mount is read-only. An active stint can also
queue one fresh portrait on its next unambiguous observation. Closed historical
stints whose full frames have expired retain their old descriptions; no missing
face or hair detail is inferred from the old torso-only crop.

```sql
SELECT session_id, description, description_language, appearance,
       input_version, prompt_version, status
FROM presenter_clothing_descriptions
ORDER BY observed_at DESC;
```

Schema setup checks whether descriptor migrations are already applied before
issuing ALTER TABLE, avoiding startup waits behind a long descriptor transaction.
This is covered by an integration check holding an inference row lock with a
200 ms lock timeout on concurrent schema setup.

## Internal-only appearance data

The user requires all clothing and appearance descriptions to remain internal,
in PostgreSQL and in Google Sheets. They must never be included in public chat.
The YouTube `!rekord` command therefore reads only the numeric record; it never
queries the description table. Public send validation also rejects structured
appearance fields before quota reservation or HTTP. Existing internal descriptions
and Sheets export remain available; no source records are deleted by this change.
