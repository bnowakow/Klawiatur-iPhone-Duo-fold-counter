"""Offline-fixture PostgreSQL verification of the public-data archive."""
from email.message import Message
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql

import ipduotest_pl


class Response(io.BytesIO):
    def __init__(self,body,url,content_type):
        super().__init__(body)
        self.url=url
        self.headers=Message()
        self.headers['Content-Type']=content_type


def check(schema):
    fixtures=Path('/fixtures/test-data/2026-10-25-ipduotest-pl')
    def offline(request,timeout):
        url=request.full_url
        if url==ipduotest_pl.SITE:
            name='home.html'
        elif url.endswith('gid=225705126'):
            name='sheet-4.html'
        elif url.endswith('gid=950352229'):
            name='sheet-3.html'
        elif url.endswith('gid=0'):
            name='sheet-1.html'
        elif url.endswith('wykres_v2.png'):
            name='chart.png'
        else:
            raise AssertionError('Unexpected fixture request: '+url)
        return Response((fixtures/name).read_bytes(),url,'image/png' if name.endswith('.png') else 'text/html')
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp)
        with patch('ipduotest_pl.urlopen',side_effect=offline):
            run=ipduotest_pl.collect(root)
        assert run['status']=='ok',run
        assert len(run['resources'])==5
        with patch('psycopg.connect',side_effect=psycopg.OperationalError('simulated outage')):
            try:
                ipduotest_pl.flush(root)
            except psycopg.OperationalError:
                pass
            else:
                raise AssertionError('Expected database outage')
        pending=root/'pending'/(run['id']+'.json')
        assert pending.exists()
        assert ipduotest_pl.flush(root)==1
        # Simulate an archive created before the source was correctly named.
        # Replaying must rename the schema without losing any stored bytes.
        with psycopg.connect() as conn:
            for suffix in ('latest', 'latest_success'):
                conn.execute(sql.SQL('ALTER VIEW {} RENAME TO {}').format(
                    sql.Identifier('ipduotest_pl_' + suffix), sql.Identifier('official_data_' + suffix)))
            for suffix in ('runs', 'blobs', 'resources'):
                conn.execute(sql.SQL('ALTER TABLE {} RENAME TO {}').format(
                    sql.Identifier('ipduotest_pl_' + suffix), sql.Identifier('official_data_' + suffix)))
        # Replay the same manifest and blobs; history must remain idempotent.
        shutil.copyfile(root/'runs'/pending.name,pending)
        assert ipduotest_pl.flush(root)==1
        with psycopg.connect() as conn:
            resources=conn.execute('SELECT resource_key,parsed,sha256 FROM ipduotest_pl_resources WHERE run_id=%s',(run['id'],)).fetchall()
            assert len(resources)==5,resources
            schedule=next(r for r in resources if r[0].endswith(':0'))
            assert schedule[1]['row_count']==42,schedule
            body=conn.execute('SELECT body FROM ipduotest_pl_blobs WHERE sha256=%s',(schedule[2],)).fetchone()[0]
            assert bytes(body)==(fixtures/'sheet-1.html').read_bytes()
            assert conn.execute('SELECT count(*) FROM ipduotest_pl_runs WHERE id=%s',(run['id'],)).fetchone()[0]==1
        report={'integration':'passed','schema':schema,'run_id':run['id'],'resources':len(resources),
                'checks':['complete sheets','raw bytes in PostgreSQL','outage retention',
                          'legacy schema migration','idempotent replay']}
        archive=Path(os.getenv('IPDUOTEST_PL_TEST_OUTPUT_DIR','/data/test-evidence/ipduotest-pl'))/run['id']
        shutil.copytree(root,archive)
        (archive/'result.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report))


def main():
    schema = 'ipduotest_pl_test_' + uuid.uuid4().hex
    with psycopg.connect() as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    with patch.dict(os.environ, {'PGOPTIONS':'-c search_path='+schema}):
        check(schema)


if __name__=='__main__':
    main()
