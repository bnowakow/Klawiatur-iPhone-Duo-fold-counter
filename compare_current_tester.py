"""Persist timestamped comparisons of published current-tester pace and our stint."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import math
import os
import uuid

CURRENT_KEY = 'sheet:1fK3Qn5pPK0sEZGayoWihVKLhizaGhnYR4iplJ7MCd_s:225705126'
SCHEMA = """
DO $$
DECLARE old_name text;
BEGIN
 FOREACH old_name IN ARRAY ARRAY['official_run_id', 'official_fpm'] LOOP
  IF EXISTS (SELECT FROM information_schema.columns
             WHERE table_schema=current_schema() AND table_name='tester_comparisons'
             AND column_name=old_name) THEN
   EXECUTE format('ALTER TABLE tester_comparisons RENAME COLUMN %I TO %I',
                  old_name, replace(old_name, 'official', 'ipduotest_pl'));
  END IF;
 END LOOP;
END $$;
CREATE TABLE IF NOT EXISTS tester_comparisons (
 id uuid PRIMARY KEY, compared_at timestamptz NOT NULL, stream_url text NOT NULL,
 ipduotest_pl_run_id uuid NOT NULL, session_id uuid, status text NOT NULL,
 ipduotest_pl_fpm double precision, recent_fpm double precision, stint_fpm double precision,
 recent_gap_fpm double precision, stint_gap_fpm double precision,
 details jsonb NOT NULL, UNIQUE (stream_url, ipduotest_pl_run_id)
);
CREATE INDEX IF NOT EXISTS tester_comparisons_session_time
 ON tester_comparisons(session_id, compared_at);
"""


class InvalidPublishedValues(ValueError):
    """The published cells are numeric but cannot describe a valid pace."""


def number(value):
    result = float(value.strip().replace('\u00a0','').replace(' ','').replace(',','.'))
    if not math.isfinite(result) or result < 0:
        raise InvalidPublishedValues(f'Published values must be finite and nonnegative (received {value!r})')
    return result


def published_values(parsed):
    row = next(row for row in parsed['rows'] if any(cell.strip() for cell in row))
    values = dict(zip(parsed['headers'],row))
    return {'fpm':number(values['Złożenia na minutę']),
            'folds':number(values['Wynik bieżącego zawodnika']),
            'minutes':number(values['Minuta pomiaru']),
            'projection':number(values['Szacunek na koniec'])}


def cadence_errors(samples, intervals=(1,2,3,4,5,10,20,30,60)):
    """Offline thinning: reconstruction error against retained accepted samples."""
    if len(samples) < 2:
        return []
    start = samples[0][0]
    points = [((ts-start).total_seconds(),value) for ts,value in samples]
    results = []
    gaps = [b[0]-a[0] for a,b in zip(points,points[1:])]
    import statistics
    reference_gap = statistics.median(gaps)
    for interval in intervals:
        if interval < reference_gap/1.25:
            results.append({'target_interval_seconds':interval, 'status':'finer_reference_required',
                            'reference_median_gap_seconds':reference_gap,
                            'reference_samples':len(points)})
            continue
        kept, bucket = [points[0]], 0
        for point in points[1:-1]:
            next_bucket = int(point[0]//interval)
            if next_bucket != bucket:
                kept.append(point)
                bucket = next_bucket
        kept.append(points[-1])
        errors, j = [], 0
        for ts,value in points:
            while j+1 < len(kept)-1 and kept[j+1][0] < ts:
                j += 1
            t0,v0 = kept[j]
            t1,v1 = kept[j+1]
            estimate = v0 + (v1-v0)*(ts-t0)/(t1-t0)
            errors.append(abs(value-estimate))
        results.append({'target_interval_seconds':interval, 'status':'evaluated',
                        'reference_median_gap_seconds':reference_gap, 'retained_samples':len(kept),
                        'reference_samples':len(points),
                        'max_gap_seconds':max(b[0]-a[0] for a,b in zip(kept,kept[1:])),
                        'mean_absolute_counter_error':sum(errors)/len(errors),
                        'max_absolute_counter_error':max(errors)})
    return results


def estimates(samples, folding_start, folding_counter):
    if not samples:
        return {'recent_fpm':None,'stint_fpm':None,'sample_count':0,'cadence_analysis':[]}
    end,value = samples[-1]
    recent = [(t,v) for t,v in samples if t >= end-timedelta(seconds=180)]
    elapsed = (recent[-1][0]-recent[0][0]).total_seconds()
    stint_elapsed = (end-folding_start).total_seconds() if folding_start else 0
    return {'recent_fpm':(value-recent[0][1])*60/elapsed if elapsed>0 else None,
            'stint_fpm':(value-folding_counter)*60/stint_elapsed if stint_elapsed>0 and folding_counter is not None else None,
            'sample_count':len(recent), 'recent_window_seconds':elapsed,
            'measured_at':end.isoformat(), 'counter':value,
            'folding_elapsed_seconds':stint_elapsed,
            'cadence_analysis':cadence_errors(recent)}


def compare(conn, stream=None, at=None):
    from psycopg.types.json import Jsonb
    stream = stream or os.getenv('STREAM_URL','https://www.youtube.com/watch?v=vaiHWiExDXg')
    at = at or datetime.now(timezone.utc)
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("tester-comparison:"+stream,))
    conn.execute(SCHEMA)
    # Read latest attempt; never silently substitute an old successful fetch.
    source = conn.execute('SELECT run_id,fetched_at,status,parsed,sha256,source_url '
                          'FROM ipduotest_pl_latest WHERE resource_key=%s',(CURRENT_KEY,)).fetchone()
    if source is None:
        return {'status':'no_published_source','stream_url':stream}
    run_id,fetched,status,parsed,sha,url = source
    existing = conn.execute('SELECT id,status,ipduotest_pl_fpm,recent_fpm,stint_fpm,details '
                            'FROM tester_comparisons WHERE stream_url=%s AND ipduotest_pl_run_id=%s',
                            (stream,run_id)).fetchone()
    if existing:
        return {'id':str(existing[0]),'status':existing[1], 'ipduotest_pl_fpm':existing[2],
                'recent_fpm':existing[3],'stint_fpm':existing[4],
                'details':existing[5], 'already_stored':True}
    details = {'source_url':url,'source_fetched_at':fetched.isoformat(),'source_sha256':sha,
               'source_age_seconds':(at-fetched).total_seconds(),'identity_verified':False,
               'interval_seconds':float(os.getenv('INTERVAL_SECONDS',4))}
    session = conn.execute('SELECT id,detected_at,folding_started_at,folding_start_counter,last_counter_at '
                           'FROM presenter_sessions WHERE stream_url=%s AND ended_at IS NULL '
                           'ORDER BY detected_at DESC LIMIT 1',(stream,)).fetchone()
    values, metrics = {}, {}
    verdict = 'compared'
    if status != 'ok' or not parsed:
        verdict = 'source_unavailable'
    else:
        try:
            values = published_values(parsed)
            details['published'] = values
        except InvalidPublishedValues as exc:
            verdict = 'source_invalid_values'
            details['error'] = str(exc)
        except (KeyError,ValueError,StopIteration,TypeError) as exc:
            verdict = 'source_parse_error'
            details['error'] = str(exc)
    max_source_age = max(float(os.getenv('IPDUOTEST_PL_INTERVAL_SECONDS',300))*2,60)
    if verdict == 'compared' and not 0 <= (at-fetched).total_seconds() <= max_source_age:
        verdict = 'source_stale'
    if session:
        sid,detected,started,start_counter,last_counter_at = session
        details.update(detected_at=detected.isoformat(),folding_started_at=started.isoformat() if started else None)
        samples = conn.execute("SELECT observed_at,accepted_counter FROM observations "
                               "WHERE stream_url=%s AND observed_at>=%s AND observed_at<=%s "
                               "AND accepted_counter IS NOT NULL AND details->>'presenter_session_id'=%s "
                               "AND COALESCE(details->>'presenter_attribution_review_required','false')='false' "
                               "AND details->>'presenter_candidate_id' IS NULL ORDER BY observed_at",
                               (stream,detected,min(fetched,last_counter_at) if last_counter_at else fetched,str(sid))).fetchall()
        metrics = estimates(samples,started,start_counter)
        details['app'] = metrics
        latest = conn.execute("SELECT details->>'presenter_attribution_review_required',clothing_status,details->>'polling_interval_seconds' "
                              "FROM observations WHERE stream_url=%s AND observed_at<=%s AND status='analyzed' "
                              "ORDER BY observed_at DESC LIMIT 1",(stream,fetched)).fetchone()
        if latest and latest[2] is not None:
            details['interval_seconds'] = float(latest[2])
        if verdict == 'compared':
            if detected > fetched:
                verdict = 'session_after_source'
            elif latest and (latest[0]=='true' or latest[1] in ('change_pending','possible_handover_review_required','camera_view_unknown_review_required')):
                verdict = 'attribution_uncertain'
            elif not samples:
                verdict = 'no_app_samples'
            elif (fetched-samples[-1][0]).total_seconds()>max(60,details['interval_seconds']*2.5):
                verdict = 'app_stale'
            elif metrics['recent_fpm'] is None or metrics['stint_fpm'] is None:
                verdict = 'insufficient_app_samples'
    elif verdict == 'compared':
        verdict = 'no_current_session'
    fingerprint = hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest() if values else None
    previous = conn.execute("SELECT details->>'published_values_hash' FROM tester_comparisons "
                            "WHERE stream_url=%s ORDER BY compared_at DESC LIMIT 1",(stream,)).fetchone()
    details['published_values_hash'] = fingerprint
    details['published_values_changed'] = previous[0]!=fingerprint if previous and fingerprint else None
    # Differences are numeric only when freshness and attribution checks pass.
    recent = metrics.get('recent_fpm') if verdict=='compared' else None
    stint = metrics.get('stint_fpm') if verdict=='compared' else None
    ipduotest_pl = values.get('fpm')
    recent_gap = recent-ipduotest_pl if recent is not None else None
    stint_gap = stint-ipduotest_pl if stint is not None else None
    if ipduotest_pl and stint_gap is not None:
        details['stint_gap_percent'] = 100*stint_gap/ipduotest_pl
    report = {'id':str(uuid.uuid4()),'status':verdict,'ipduotest_pl_fpm':ipduotest_pl,
              'recent_fpm':recent,'stint_fpm':stint,'recent_gap_fpm':recent_gap,
              'stint_gap_fpm':stint_gap,'details':details}
    conn.execute('INSERT INTO tester_comparisons VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                 'ON CONFLICT (stream_url,ipduotest_pl_run_id) DO NOTHING',
                 (report['id'],at,stream,run_id,session[0] if session else None,verdict,
                  ipduotest_pl,recent,stint,recent_gap,stint_gap,Jsonb(details)))
    return report


def main():
    import psycopg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stored',action='store_true',help='Use the latest stored source attempt without fetching')
    parser.add_argument('--stream',default=None)
    args = parser.parse_args()
    if not args.stored:
        from ipduotest_pl import archive_root,collect,flush
        root = archive_root()
        collect(root)
        flush(root)
    with psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=10) as conn:
        if args.stored:
            from ipduotest_pl import SCHEMA as source_schema
            conn.execute(source_schema)
        report = compare(conn,args.stream)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='compared' else 2


if __name__=='__main__':
    raise SystemExit(main())
