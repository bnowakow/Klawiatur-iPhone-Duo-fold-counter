"""Local, asynchronous clothing descriptions for anonymous folding stints."""
import base64
from datetime import datetime, timezone
import io
import json
import logging
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

MODEL = 'moondream:1.8b'
PROMPT = ('Describe only the visible clothing of the person in this image in one short sentence. '
          'Include garment colors and types (for example, a white short-sleeved T-shirt). '
          'Do not describe the person, face, identity, objects, or background. '
          'Do not guess hidden clothing. If clothing type is unclear, say top. '
          'If no clothing is clearly visible, say Clothing not clearly visible.')
SCHEMA = """
CREATE TABLE IF NOT EXISTS presenter_clothing_descriptions (
 session_id uuid PRIMARY KEY REFERENCES presenter_sessions(id),
 observation_id uuid NOT NULL, observed_at timestamptz NOT NULL,
 camera_view text NOT NULL, crop_roi jsonb NOT NULL, image_sha256 text NOT NULL,
 image bytea NOT NULL, status text NOT NULL DEFAULT 'pending',
 description text, model text, prompt_version integer NOT NULL DEFAULT 1,
 generated_at timestamptz, attempts integer NOT NULL DEFAULT 0,
 retry_at timestamptz NOT NULL DEFAULT now(), last_error text, response jsonb
);
"""


def prepare(image, obs, sessions):
    """Queue once per active stint, only after its first accepted increase."""
    current = sessions.get('current')
    details = obs['details']
    camera = details.get('camera_view', {})
    if (not current or not current.get('folding_started_at') or not current.get('confirmed_at')
            or current.get('clothing_description_queued')
            or sessions.get('candidate') or details.get('presenter_attribution_review_required')
            or camera.get('scene_kind') == 'scheduled_break'):
        return
    rois = {'presenter_main': (.36, .35, .78, .83),
            'presenter_inset': (.785, .775, .90, .925)}
    roi = rois.get(camera.get('view'))
    if roi is None:
        return
    image = image.convert('RGB').copy()
    if camera['view'] == 'presenter_main':
        # Mask the table-camera inset so it cannot confuse the clothing caption.
        ImageDraw.Draw(image).rectangle((int(image.width*.64), int(image.height*.63),
                                         image.width, image.height), fill='black')
    crop = image.crop(tuple(round(n*d) for n, d in zip(roi, (image.width,image.height)*2)))
    crop.thumbnail((768, 768))
    buffer = io.BytesIO()
    crop.save(buffer, format='JPEG', quality=88)
    import hashlib
    body = buffer.getvalue()
    details['clothing_description_job'] = {
        'session_id': current['id'], 'observation_id': obs['id'],
        'observed_at': obs['observed_at'], 'camera_view': camera['view'],
        'crop_roi': roi, 'image_sha256': hashlib.sha256(body).hexdigest(),
        'image_base64': base64.b64encode(body).decode('ascii')}
    current['clothing_description_queued'] = True


def save_job(conn, obs):
    job = obs['details'].get('clothing_description_job')
    if not job:
        return
    from psycopg.types.json import Jsonb
    conn.execute('INSERT INTO presenter_clothing_descriptions '
                 '(session_id,observation_id,observed_at,camera_view,crop_roi,image_sha256,image) '
                 'VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id) DO NOTHING',
                 (job['session_id'], job['observation_id'], job['observed_at'], job['camera_view'],
                  Jsonb(job['crop_roi']), job['image_sha256'], base64.b64decode(job['image_base64'], validate=True)))


def generate(body, base_url, model):
    payload = {'model':model, 'prompt':PROMPT, 'images':[base64.b64encode(body).decode('ascii')],
               'stream':False, 'keep_alive':0,
               'options':{'temperature':0, 'num_predict':80, 'num_thread':2}}
    request = Request(base_url.rstrip('/')+'/api/generate', data=json.dumps(payload).encode(),
                      headers={'Content-Type':'application/json'})
    with urlopen(request, timeout=180) as response:
        result = json.load(response)
    text = ' '.join(result.get('response','').split())
    if not result.get('done') or not text or len(text) > 600:
        raise ValueError('Incomplete or invalid clothing description')
    return text, {k:result.get(k) for k in ('model','done_reason','total_duration','eval_count')}


def process_one():
    import psycopg
    from psycopg.types.json import Jsonb
    with psycopg.connect(os.getenv('DATABASE_URL',''), connect_timeout=10) as conn:
        # A dedicated row lock prevents duplicate workers. No supervisor row is locked.
        row = conn.execute('SELECT session_id,image FROM presenter_clothing_descriptions '
                           "WHERE status IN ('pending','retry') AND retry_at<=now() "
                           'ORDER BY observed_at FOR UPDATE SKIP LOCKED LIMIT 1').fetchone()
        if row is None:
            return False
        model = os.getenv('CLOTHING_DESCRIPTION_MODEL',MODEL)
        try:
            text, response = generate(bytes(row[1]), os.getenv('CLOTHING_DESCRIPTION_URL','http://vision:11434'), model)
            status = 'uncertain' if 'not clearly visible' in text.lower() else 'completed'
            conn.execute('UPDATE presenter_clothing_descriptions SET description=%s,status=%s,model=%s,'
                         'response=%s,generated_at=now(),attempts=attempts+1,last_error=NULL WHERE session_id=%s',
                         (text,status,model,Jsonb(response),row[0]))
            logging.info('Clothing description stored for session %s: %s',row[0],text)
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[:1000]
            conn.execute("UPDATE presenter_clothing_descriptions SET status='retry',model=%s,"
                         "attempts=attempts+1,last_error=%s,retry_at=now()+interval '5 minutes' WHERE session_id=%s",
                         (model,error,row[0]))
            logging.error('Clothing description failed for session %s: %s',row[0],error)
    return True


def main():
    import psycopg
    import supervisor
    logging.basicConfig(level=logging.INFO)
    with psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=10) as conn:
        supervisor.ensure_schema(conn)
    while True:
        try:
            if not process_one():
                time.sleep(5)
        except Exception:
            logging.exception('Clothing description worker failed')
            time.sleep(10)


if __name__ == '__main__':
    main()
