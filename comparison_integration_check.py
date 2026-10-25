"""Verify persisted comparisons, freshness and attribution in an isolated schema."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

import supervisor
import official_data
from compare_current_tester import compare,CURRENT_KEY


def main():
    schema='comparison_test_'+uuid.uuid4().hex
    with psycopg.connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ,{'PGOPTIONS':'-c search_path='+schema}):
        with psycopg.connect() as conn:
            conn.execute(supervisor.SCHEMA)
            conn.execute(official_data.SCHEMA)
            t=datetime.now(timezone.utc)
            stream='comparison-fixture'
            sid=str(uuid.uuid4())
            # A 5-minute stint at 45/min; source rounds to 40/min.
            conn.execute('INSERT INTO presenter_sessions '
                         '(id,stream_url,detected_at,confirmed_at,detection_reason,first_observation_id,'
                         'folding_started_at,folding_start_counter,last_counter_at,version_at) '
                         'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                         (sid,stream,t-timedelta(seconds=300),t,'fixture',str(uuid.uuid4()),
                          t-timedelta(seconds=300),1000,t,t))
            for i in range(31):
                details={'presenter_session_id':sid,'presenter_attribution_review_required':False}
                conn.execute('INSERT INTO observations (id,stream_url,observed_at,status,accepted_counter,clothing_status,details) '
                             'VALUES (%s,%s,%s,%s,%s,%s,%s)',
                             (str(uuid.uuid4()),stream,t-timedelta(seconds=300-i*10),'analyzed',1000+i*7.5,
                              'no_clothing_change_detected',Jsonb(details)))
            # Integer counters avoid fractional folds: use 60/min for exact SQL checks.
            conn.execute('UPDATE observations SET accepted_counter=1000+EXTRACT(EPOCH FROM (observed_at-%s))::bigint '
                         'WHERE stream_url=%s',(t-timedelta(seconds=300),stream))
            parsed={'headers':['Wynik bieżącego zawodnika','Minuta pomiaru','Szacunek na koniec','Złożenia na minutę'],
                    'rows':[['200','5','2400','40']]}
            def source(at,status='ok'):
                rid=str(uuid.uuid4())
                conn.execute('INSERT INTO official_data_runs VALUES (%s,%s,%s,%s,%s,NULL)',(rid,at,at,official_data.SITE,status))
                conn.execute('INSERT INTO official_data_resources VALUES (%s,%s,%s,%s,%s,NULL,NULL,%s,%s)',
                             (rid,CURRENT_KEY,'fixture-source',at,status,Jsonb({}),Jsonb(parsed)))
                return rid
            source(t)
            result=compare(conn,stream,t)
            assert result['status']=='compared',result
            assert result['stint_fpm']==60 and result['recent_fpm']==60,result
            assert result['stint_gap_fpm']==20,result
            assert compare(conn,stream,t)['already_stored']
            assert conn.execute('SELECT count(*) FROM tester_comparisons').fetchone()[0]==1
            source(t+timedelta(seconds=1))
            stale=compare(conn,stream,t+timedelta(seconds=1000))
            assert stale['status']=='source_stale' and stale['stint_gap_fpm'] is None,stale
            conn.execute("UPDATE observations SET details=jsonb_set(details,'{presenter_attribution_review_required}','true') "
                         "WHERE observed_at=%s",(t,))
            source(t+timedelta(seconds=2))
            uncertain=compare(conn,stream,t+timedelta(seconds=2))
            assert uncertain['status']=='attribution_uncertain' and uncertain['stint_gap_fpm'] is None,uncertain
            conn.execute("UPDATE observations SET details=jsonb_set(details,'{presenter_attribution_review_required}','false')")
            source(t+timedelta(seconds=100))
            assert compare(conn,stream,t+timedelta(seconds=100))['status']=='app_stale'
            source(t+timedelta(seconds=101),'error')
            assert compare(conn,stream,t+timedelta(seconds=101))['status']=='source_unavailable'
        root=Path(os.getenv('COMPARISON_TEST_OUTPUT_DIR','/data/test-evidence/comparisons'))/schema
        root.mkdir(parents=True,exist_ok=True)
        report={'integration':'passed','schema':schema,'comparison':result,
                'checks':['timestamp alignment','recent and stint windows','idempotent rerun',
                          'source freshness','app freshness','attribution uncertainty','source failure']}
        (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps({'integration':'passed','schema':schema,'evidence_path':str(root)}))


if __name__=='__main__':
    main()
