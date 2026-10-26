"""Adaptive command policy in session-local table copies; no production changes."""
from datetime import datetime,timedelta,timezone
import json,os,uuid
from pathlib import Path
import youtube_bot as bot
import youtube_command_policy as policy
import youtube_quota as quota


def main():
    bot.initialize()
    channel='integration:command-policy:'+uuid.uuid4().hex
    with quota.connect() as c:
        for table in ['youtube_bot_commands','youtube_bot_cooldowns','youtube_bot_command_events',
                      'youtube_posting_policies','youtube_operational_incidents']:
            c.execute(f'CREATE TEMP TABLE {table} (LIKE public.{table} INCLUDING ALL)')
        c.execute('INSERT INTO youtube_bot_commands SELECT * FROM public.youtube_bot_commands')
        c.execute('INSERT INTO youtube_bot_command_events SELECT * FROM public.youtube_bot_command_events')
        c.execute('INSERT INTO youtube_bot_cooldowns SELECT * FROM public.youtube_bot_cooldowns')
        change=policy.enable_trial(c,channel)
        assert change['cooldown_seconds']==60
        rows=c.execute('SELECT cooldown_seconds,disclaimer FROM youtube_bot_commands').fetchall()
        assert all(seconds==60 and 'raz na minutę' in disclaimer for seconds,disclaimer in rows)
        assert policy.on_post_rate_limit(c,'unmonitored-channel',uuid.uuid4()) is None
        trigger=uuid.uuid4()
        changed=policy.on_post_rate_limit(c,channel,trigger)
        assert changed['cooldown_seconds']==600 and changed['request_id']==str(trigger)
        rows=c.execute('SELECT cooldown_seconds,disclaimer FROM youtube_bot_commands').fetchall()
        assert all(seconds==600 and 'raz na 10 minut' in disclaimer for seconds,disclaimer in rows)
        state=c.execute('SELECT value FROM youtube_posting_policies WHERE policy_key=%s',(policy.policy_key(channel),)).fetchone()[0]
        assert state['active_seconds']==600 and not state['automatically_reenable_trial']
        assert policy.on_post_rate_limit(c,channel,uuid.uuid4()) is None
        assert c.execute('SELECT count(*) FROM youtube_operational_incidents').fetchone()[0]==2
    result={'passed':True,'real_google_requests':0,'production_policy_changes':0,
            'checks':['one-minute policy and Polish disclaimer','unmonitored accounts ignored',
                      'rate-limit failure restores all commands to ten minutes','rejection request linked in audit',
                      'repeated rejection idempotent','fallback persists without automatic relaxation']}
    root=Path(os.getenv('DATA_DIR','data'))/'test-evidence'/'youtube-command-policy'/uuid.uuid4().hex
    root.mkdir(parents=True,exist_ok=True);(root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
