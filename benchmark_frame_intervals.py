"""Measure one-second persistent live capture plus the full Compose analysis/DB path."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone, timedelta
import io
import json
import math
import os
from pathlib import Path
import resource
import statistics
import subprocess
import time
from unittest.mock import patch
import uuid

import supervisor
from compare_current_tester import cadence_errors


def percentile(values, q):
    return sorted(values)[min(len(values)-1,round((len(values)-1)*q))] if values else None


def store_result(report, root):
    import psycopg
    from psycopg.types.json import Jsonb
    with psycopg.connect(os.getenv('DATABASE_URL',''),connect_timeout=10) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS frame_interval_benchmarks ("
                     "id uuid PRIMARY KEY, started_at timestamptz NOT NULL, "
                     "evidence_path text NOT NULL, result jsonb NOT NULL)")
        conn.execute("INSERT INTO frame_interval_benchmarks VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (id) DO NOTHING",
                     (report['run_id'],report['started_at'],str(root),Jsonb(report)))


def run_serial(cfg, args, root, run_id, source_stream):
    """Time the actual restart-per-frame capture + analysis + commit path."""
    started = datetime.now(timezone.utc)
    cfg.update(stream='benchmark:serial:'+run_id, interval=args.interval, data=root/'evidence')
    # Only the benchmark stream key changes; capture still reads the actual URL.
    original_capture = supervisor.capture
    def live_capture(_stream, destination):
        return original_capture(source_stream, destination)
    timings, records, starts = [], [], []
    begin = time.monotonic()
    cpu_start = resource.getrusage(resource.RUSAGE_SELF)
    child_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    with patch('supervisor.capture', side_effect=live_capture):
        while time.monotonic()-begin < args.seconds:
            cycle_start = time.monotonic()
            starts.append(cycle_start-begin)
            with redirect_stdout(io.StringIO()):
                obs = supervisor.cycle(cfg)
            elapsed = time.monotonic()-cycle_start
            timings.append(elapsed)
            records.append({'cycle_seconds':elapsed, 'observation':obs})
            with (root/'observations.jsonl').open('a') as output:
                output.write(json.dumps(records[-1])+'\n')
            print(json.dumps({'frames':len(records),'cycle_seconds':elapsed}),flush=True)
            remaining = args.seconds-(time.monotonic()-begin)
            if remaining>0:
                time.sleep(min(remaining,max(0,args.interval-elapsed)))
    wall = time.monotonic()-begin
    cpu = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    report = {'run_id':run_id,'mode':'serial','source_stream':source_stream,
              'benchmark_stream':cfg['stream'],'started_at':started.isoformat(),
              'target_interval_seconds':args.interval, 'requested_duration_seconds':args.seconds,
              'frames':len(records),'accepted_samples':sum(r['observation'].get('accepted_counter') is not None for r in records),
              'wall_seconds':wall,'cycle_mean_seconds':statistics.mean(timings),
              'cycle_p95_seconds':percentile(timings,.95),'cycle_max_seconds':max(timings),
              'cycles_over_target_interval':sum(t>args.interval for t in timings),
              'actual_start_gap_mean_seconds':statistics.mean([b-a for a,b in zip(starts,starts[1:])]) if len(starts)>1 else None,
              'cpu_seconds':cpu.ru_utime+cpu.ru_stime-cpu_start.ru_utime-cpu_start.ru_stime+
                            children.ru_utime+children.ru_stime-child_start.ru_utime-child_start.ru_stime,
              'errors':sum(r['observation']['status'] in ('error','database_unavailable') for r in records),
              'note':'Full production capture path on isolated benchmark stream; no parallel cycles or nominal timestamp substitution.'}
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    store_result(report,root)
    print(json.dumps(dict(report,evidence_path=str(root)),indent=2))
    return 1 if report['errors'] else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=60)
    parser.add_argument('--serial-checks',type=int,default=2)
    parser.add_argument('--interval',type=int,choices=(1,2,3,4,5),default=1)
    parser.add_argument('--mode',choices=('persistent','serial'),default='persistent')
    args=parser.parse_args()
    if not 10<=args.seconds<=300:
        parser.error('Use 10 to 300 seconds')
    if not 0<=args.serial_checks<=3:
        parser.error('Use 0 to 3 serial checks')
    cfg=supervisor.config()
    source_stream=cfg['stream']
    run_id=str(uuid.uuid4())
    root=cfg['data']/'benchmarks'/(f'{args.mode}-interval-{args.interval}s-'+run_id)
    requested_frames=math.ceil(args.seconds/args.interval)
    root.mkdir(parents=True)
    if args.mode=='serial':
        return run_serial(cfg,args,root,run_id,source_stream)
    started=datetime.now(timezone.utc)
    start=time.monotonic()
    cpu_start=resource.getrusage(resource.RUSAGE_CHILDREN)
    own_start=resource.getrusage(resource.RUSAGE_SELF)
    serial=[]
    for i in range(args.serial_checks):
        before=time.monotonic()
        try:
            supervisor.capture(source_stream,root/f'serial-{i}.jpg')
            serial.append({'seconds':time.monotonic()-before,'status':'ok'})
        except Exception as exc:
            serial.append({'seconds':time.monotonic()-before,'status':'error','error':str(exc)})
        print(json.dumps({'phase':'serial_capture','sample':serial[-1]}),flush=True)
    metadata_start=time.monotonic()
    meta=subprocess.run(['yt-dlp','--skip-download','--dump-single-json','--no-playlist',
                         '--socket-timeout','15','--retries','1',source_stream],capture_output=True,
                         text=True,timeout=60)
    if meta.returncode:
        raise RuntimeError('Stream metadata lookup failed (signed URLs omitted)')
    info=json.loads(meta.stdout)
    formats=[f for f in info['formats'] if f.get('height') and f['height']<=1080 and f.get('url')
             and f.get('protocol','').startswith('m3u8')]
    if not info.get('is_live') or not formats:
        raise RuntimeError('No live HLS format available')
    selected=max(formats,key=lambda f:f['height'])
    metadata_seconds=time.monotonic()-metadata_start
    cfg.update(stream=f'benchmark:interval-{args.interval}s:'+run_id,interval=args.interval,data=root/'evidence')
    cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-rw_timeout','20000000',
         '-live_start_index','-1','-i',selected['url'],'-t',str(args.seconds),
         '-vf',f'fps=1/{args.interval}','-frames:v',str(requested_frames),'-f','image2pipe','-vcodec','mjpeg','-q:v','2','pipe:1']
    samples,records,buffer=[],[],b''
    capture_start=time.monotonic()
    # stderr stays captured and is never printed: media errors can contain signed URLs.
    with (root/'ffmpeg-errors.private.log').open('wb') as errors:
        process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=errors)
        try:
            while True:
                chunk=os.read(process.stdout.fileno(),65536)
                if not chunk:
                    break
                buffer+=chunk
                while b'\xff\xd9' in buffer:
                    end=buffer.index(b'\xff\xd9')+2
                    frame,buffer=buffer[:end],buffer[end:]
                    if not frame.startswith(b'\xff\xd8'):
                        raise RuntimeError('Unexpected JPEG framing')
                    index=len(records)
                    received=datetime.now(timezone.utc)
                    received_elapsed=time.monotonic()-capture_start
                    path=root/f'input-{index:04d}.jpg'
                    path.write_bytes(frame)
                    # Distinct media-second positions; HLS segments can arrive in bursts.
                    # These fixture timestamps are nominal, not broadcaster UTC times.
                    if not records:
                        nominal_start=received
                    nominal=nominal_start+timedelta(seconds=index*args.interval)
                    log=io.StringIO()
                    analysis_start=time.monotonic()
                    with patch('supervisor.datetime',wraps=datetime) as clock,redirect_stdout(log):
                        clock.now.return_value=nominal
                        obs=supervisor.cycle(cfg,path)
                    analysis_seconds=time.monotonic()-analysis_start
                    record={'index':index,'received_at':received.isoformat(),
                            'received_elapsed_seconds':received_elapsed,
                            'nominal_media_timestamp':nominal.isoformat(),
                            'analysis_and_db_seconds':analysis_seconds,'observation':obs}
                    records.append(record)
                    with (root/'observations.jsonl').open('a') as output:
                        output.write(json.dumps(record)+'\n')
                    if obs.get('accepted_counter') is not None:
                        samples.append((nominal,obs['accepted_counter']))
                    if len(records)%15==0:
                        print(json.dumps({'frames':len(records),'last_analysis_seconds':analysis_seconds}),flush=True)
            exit_code=process.wait(timeout=15)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
    # Keep sanitized error evidence; do not retain signed command/URL material.
    error_path=root/'ffmpeg-errors.private.log'
    error_bytes=error_path.stat().st_size
    error_path.unlink()
    wall=time.monotonic()-start
    child=resource.getrusage(resource.RUSAGE_CHILDREN)
    own=resource.getrusage(resource.RUSAGE_SELF)
    cpu=(child.ru_utime+child.ru_stime-cpu_start.ru_utime-cpu_start.ru_stime+
         own.ru_utime+own.ru_stime-own_start.ru_utime-own_start.ru_stime)
    timings=[r['analysis_and_db_seconds'] for r in records]
    arrivals=[b['received_elapsed_seconds']-a['received_elapsed_seconds'] for a,b in zip(records,records[1:])]
    report={'run_id':run_id,'mode':'persistent','benchmark_stream':cfg['stream'],'source_stream':source_stream,
            'started_at':started.isoformat(),'height':selected['height'],'target_interval_seconds':args.interval,
            'requested_frames':requested_frames,'frames':len(records),'accepted_samples':len(samples),
            'serial_capture_checks':serial,
            'metadata_lookup_seconds':metadata_seconds,'wall_seconds':wall,
            'persistent_pipeline_wall_seconds':time.monotonic()-capture_start,'cpu_seconds':cpu,
            'average_cpu_cores_used':cpu/wall,'python_peak_rss_platform_units':own.ru_maxrss,
            'analysis_mean_seconds':statistics.mean(timings) if timings else None,
            'analysis_p95_seconds':percentile(timings,.95),'analysis_max_seconds':max(timings) if timings else None,
            'analyses_over_one_second':sum(t>1 for t in timings),
            'analyses_over_target_interval':sum(t>args.interval for t in timings),
            'arrival_gap_p50_seconds':percentile(arrivals,.5),
            'arrival_gap_p95_seconds':percentile(arrivals,.95),
            'arrival_gap_max_seconds':max(arrivals) if arrivals else None,
            'ffmpeg_exit_code':exit_code,'ffmpeg_error_bytes':error_bytes,
            'cadence_analysis':cadence_errors(samples),
            'note':'Persistent HLS pipeline; nominal media positions may arrive in segment bursts. Fixture observations use an isolated benchmark stream. Does not change live polling.'}
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    store_result(report, root)
    print(json.dumps(dict(report,evidence_path=str(root)),indent=2))
    return 0 if exit_code==0 and len(records)==requested_frames else 1


if __name__=='__main__':
    raise SystemExit(main())
