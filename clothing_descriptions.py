"""Local, asynchronous clothing descriptions for anonymous folding stints."""
import base64
from datetime import datetime, timezone
import io
import json
import logging
import os
import re
from pathlib import Path
import time
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

MODEL = 'moondream:1.8b'
UNCERTAIN = 'Ubiór nie jest wyraźnie widoczny.'
PROMPT = 'What clothing is the person wearing? Describe the colors and garment types.'
SCHEMA = """
CREATE TABLE IF NOT EXISTS presenter_clothing_descriptions (
 session_id uuid PRIMARY KEY REFERENCES presenter_sessions(id),
 observation_id uuid NOT NULL, observed_at timestamptz NOT NULL,
 camera_view text NOT NULL, crop_roi jsonb NOT NULL, image_sha256 text NOT NULL,
 image bytea NOT NULL, status text NOT NULL DEFAULT 'pending',
 description text, description_language text NOT NULL DEFAULT 'pl', model text, prompt_version integer NOT NULL DEFAULT 1,
 generated_at timestamptz, attempts integer NOT NULL DEFAULT 0,
 retry_at timestamptz NOT NULL DEFAULT now(), last_error text, response jsonb
);
ALTER TABLE presenter_clothing_descriptions
 ADD COLUMN IF NOT EXISTS description_language text NOT NULL DEFAULT 'en';
ALTER TABLE presenter_clothing_descriptions ALTER COLUMN description_language SET DEFAULT 'pl';
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
    crop.thumbnail((384, 384))
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


def public_details(obs):
    details = dict(obs['details'])
    if details.get('clothing_description_job'):
        details['clothing_description_job'] = {
            k:v for k,v in details['clothing_description_job'].items() if k != 'image_base64'}
    return details


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


# Keep only garment phrases: small models sometimes append watches or facial details.
_ADJECTIVES = ('white|black|grey|gray|red|blue|green|yellow|orange|purple|pink|brown|'
               'beige|cream|navy|olive|maroon|burgundy|teal|khaki|light|dark|off|'
               'short|long|sleeved|sleeve|striped|plain|printed|patterned|checked|'
               'cotton|knitted|knit|denim|hooded|sleeveless|zip|up|button|collared')
_GARMENTS = re.compile(r'\b(?:(?:' + _ADJECTIVES + r')[ -]+){0,7}'
                       r'(?:t[ -]?shirt|sweatshirt|sweater|hoodie|pullover|cardigan|'
                       r'jacket|shirt|blouse|vest|coat|top|dress|polo)\b', re.I)


# Deterministic localization keeps the small vision model's tested English prompt,
# but every user-facing description is stored in Polish, with noun agreement.
_NOUNS = {
    't-shirt': ('koszulka','f'), 't shirt': ('koszulka','f'), 'tshirt': ('koszulka','f'),
    'shirt': ('koszula','f'), 'sweatshirt': ('bluza','f'), 'sweater': ('sweter','m'),
    'hoodie': ('bluza z kapturem','f'), 'pullover': ('sweter','m'),
    'cardigan': ('kardigan','m'), 'jacket': ('kurtka','f'), 'blouse': ('bluzka','f'),
    'vest': ('kamizelka','f'), 'coat': ('płaszcz','m'), 'top': ('góra stroju','f'),
    'dress': ('sukienka','f'), 'polo': ('koszulka polo','f')}
_COLORS = {
    'white':('biały','biała'), 'black':('czarny','czarna'),
    'grey':('szary','szara'), 'gray':('szary','szara'),
    'red':('czerwony','czerwona'), 'blue':('niebieski','niebieska'),
    'green':('zielony','zielona'), 'yellow':('żółty','żółta'),
    'orange':('pomarańczowy','pomarańczowa'), 'purple':('fioletowy','fioletowa'),
    'pink':('różowy','różowa'), 'brown':('brązowy','brązowa'),
    'beige':('beżowy','beżowa'), 'cream':('kremowy','kremowa'),
    'navy':('granatowy','granatowa'), 'olive':('oliwkowy','oliwkowa'),
    'maroon':('kasztanowy','kasztanowa'), 'burgundy':('bordowy','bordowa'),
    'teal':('turkusowy','turkusowa'), 'khaki':('khaki','khaki')}


def polish_phrase(phrase):
    kind = next(key for key in sorted(_NOUNS,key=len,reverse=True)
                if phrase == key or phrase.endswith(' '+key))
    noun, gender = _NOUNS[kind]
    modifiers = phrase[:-len(kind)].strip().replace('-', ' ').split()
    index = 1 if gender == 'f' else 0
    colors, adjectives, endings = [], [], []
    for i, word in enumerate(modifiers):
        if word in _COLORS:
            if word == 'blue' and 'navy' in modifiers:
                continue
            color = _COLORS[word][index]
            previous = modifiers[i-1] if i else ''
            if previous in {'light','dark'}:
                color = ('jasno' if previous == 'light' else 'ciemno')+color
            colors.append(color)
    if 'off' in modifiers and 'white' in modifiers:
        colors = []
        endings.append('w kolorze złamanej bieli')
    for english, male, female in [('plain','gładki','gładka'),
                                  ('cotton','bawełniany','bawełniana'),
                                  ('denim','dżinsowy','dżinsowa'),
                                  ('knit','dzianinowy','dzianinowa'),
                                  ('knitted','dzianinowy','dzianinowa')]:
        if english in modifiers:
            adjectives.append(female if gender == 'f' else male)
    if not colors and 'off' not in modifiers:
        if 'light' in modifiers: adjectives.append('jasna' if gender == 'f' else 'jasny')
        if 'dark' in modifiers: adjectives.append('ciemna' if gender == 'f' else 'ciemny')
    if 'short' in modifiers and any(w in modifiers for w in ('sleeve','sleeved')):
        endings.append('z krótkim rękawem')
    if 'long' in modifiers and any(w in modifiers for w in ('sleeve','sleeved')):
        endings.append('z długim rękawem')
    for english, polish in [('striped','w paski'), ('checked','w kratę'),
                             ('printed','z nadrukiem'), ('patterned','we wzory'),
                             ('sleeveless','bez rękawów'), ('collared','z kołnierzykiem')]:
        if english in modifiers: endings.append(polish)
    if 'hooded' in modifiers and 'z kapturem' not in noun:
        endings.append('z kapturem')
    if 'zip' in modifiers:
        endings.append('rozpinana' if gender == 'f' else 'rozpinany')
    if 'button' in modifiers:
        endings.append('zapinana na guziki' if gender == 'f' else 'zapinany na guziki')
    return ' '.join(dict.fromkeys(colors+adjectives+[noun]+endings))


def clothing_only(text):
    if any(term in text.lower() for term in ('not visible','not clearly visible','cannot tell',
                                             "can't tell",'no person','unclear')):
        return UNCERTAIN
    phrases = list(dict.fromkeys(polish_phrase(match.group().lower())
                                 for match in _GARMENTS.finditer(text)))
    return (', '.join(phrases[:3]).capitalize()+'.') if phrases else UNCERTAIN


def migrate_polish(conn):
    """Translate existing descriptions without rerunning inference or losing history."""
    from psycopg.types.json import Jsonb
    rows = conn.execute('SELECT session_id,description,response FROM presenter_clothing_descriptions '
                        "WHERE description IS NOT NULL AND description_language<>'pl' "
                        'FOR UPDATE SKIP LOCKED').fetchall()
    for session, old_text, response in rows:
        response = dict(response or {})
        text = clothing_only(response.get('raw_description') or old_text)
        response.setdefault('previous_description',old_text)
        response.update(description_language='pl', localization_version=1)
        conn.execute("UPDATE presenter_clothing_descriptions SET description=%s,description_language='pl',"
                     'response=%s,status=%s WHERE session_id=%s',
                     (text,Jsonb(response),'uncertain' if text == UNCERTAIN else 'completed',session))
    return len(rows)


def generate(body, base_url, model):
    payload = {'model':model, 'prompt':PROMPT, 'images':[base64.b64encode(body).decode('ascii')],
               'stream':False, 'keep_alive':0,
               'options':{'temperature':0, 'num_predict':80, 'num_thread':2, 'num_ctx':2048}}
    request = Request(base_url.rstrip('/')+'/api/generate', data=json.dumps(payload).encode(),
                      headers={'Content-Type':'application/json'})
    with urlopen(request, timeout=180) as response:
        result = json.load(response)
    text = ' '.join(result.get('response','').split())
    if not result.get('done') or not text or len(text) > 600:
        raise ValueError('Incomplete or invalid clothing description')
    metadata = {k:result.get(k) for k in ('model','done_reason','total_duration','eval_count')}
    metadata.update(raw_description=text, description_language='pl', localization_version=1)
    return clothing_only(text), metadata


def process_one():
    import psycopg
    from psycopg.types.json import Jsonb
    with psycopg.connect(os.getenv('DATABASE_URL',''), connect_timeout=10) as conn:
        # A dedicated row lock prevents duplicate workers. No supervisor row is locked.
        row = conn.execute('SELECT d.session_id,d.image FROM presenter_clothing_descriptions d '
                           'JOIN presenter_sessions s ON s.id=d.session_id '
                           "WHERE d.status IN ('pending','retry') AND d.retry_at<=now() AND s.stream_url=%s "
                           'ORDER BY d.observed_at FOR UPDATE OF d SKIP LOCKED LIMIT 1',
                           (os.getenv('STREAM_URL','https://www.youtube.com/watch?v=vaiHWiExDXg'),)).fetchone()
        if row is None:
            return False
        model = os.getenv('CLOTHING_DESCRIPTION_MODEL',MODEL)
        try:
            text, response = generate(bytes(row[1]), os.getenv('CLOTHING_DESCRIPTION_URL','http://vision:11434'), model)
            status = 'uncertain' if text == UNCERTAIN else 'completed'
            conn.execute('UPDATE presenter_clothing_descriptions SET description=%s,status=%s,model=%s,'
                         "response=%s,description_language='pl',generated_at=now(),attempts=attempts+1,last_error=NULL WHERE session_id=%s",
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
        logging.info("Existing descriptions converted to Polish: %s", migrate_polish(conn))
    while True:
        try:
            if not process_one():
                time.sleep(5)
        except Exception:
            logging.exception('Clothing description worker failed')
            time.sleep(10)


if __name__ == '__main__':
    main()
