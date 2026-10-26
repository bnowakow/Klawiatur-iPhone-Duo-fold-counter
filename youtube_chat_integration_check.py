"""Real PostgreSQL delivery checks with simulated Google responses; never posts."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
import uuid
import youtube_chat as chat
import youtube_quota as quota


def response(message_id):
    result = MagicMock()
    result.__enter__.return_value.read.return_value = json.dumps({'id': message_id}).encode()
    result.__enter__.return_value.status = 200
    return result


def main():
    run = str(uuid.uuid4())
    project = 'integration:chat:'+run
    channel, chat_id = 'test-channel:'+run, 'test-chat:'+run
    stream = 'https://youtu.be/vaiHWiExDXg'
    quota.configure(project, 1000)
    chat.initialize()
    target = {'channel': {'id': channel}, 'chat_id': chat_id}
    def send(key, text='Simulated integration fixture'):
        return chat.send(project, stream, text, run+':'+key, channel, chat_id,
                         source_kind='command', source_id='incoming:'+key)
    checks = []
    with patch.dict(os.environ, {'YOUTUBE_CHAT_ENABLED': 'true'}), patch.object(chat, 'context', return_value=target), patch.object(chat.auth, 'token', return_value='fake-token'):
        with patch.object(quota, 'urlopen', return_value=response('simulated-message')) as network:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: send('same'), range(2)))
            assert network.call_count == 1
            assert any(result['status'] == 'sent' for result in results)
            assert send('same')['status'] == 'sent'
            try:
                send('same', 'Different content')
                raise AssertionError('Key reuse with different content accepted')
            except ValueError:
                pass
        checks.append('concurrent and repeated keys publish once; mismatched reuse rejected')
        for key, error, expected in [
            ('denied', HTTPError('url', 403, 'Forbidden', {}, io.BytesIO(b'{"error":{"errors":[{"reason":"forbidden"}]}}')), 'rejected'),
            ('timeout', TimeoutError('fake-secret'), 'unknown'),
            ('server', HTTPError('url', 503, 'Unavailable', {}, io.BytesIO(b'{}')), 'unknown'),
        ]:
            with patch.object(quota, 'urlopen', side_effect=error) as network:
                result = send(key)
                assert result['status'] == expected, result
                assert send(key)['status'] == expected
                assert network.call_count == 1
        checks.append('HTTP errors and ambiguous responses retained without resend')
        with patch.dict(os.environ, {'YOUTUBE_CHAT_ENABLED': 'false'}), patch.object(quota, 'urlopen') as network:
            try:
                send('disabled')
                raise AssertionError('Disabled sending accepted')
            except ValueError:
                pass
            network.assert_not_called()
        checks.append('send opt-in enforced')
        # Simulate a crash after Google success was recorded but before delivery completion.
        with patch.object(quota, 'urlopen', return_value=response('recovery-id')):
            result = send('recovery')
        with quota.connect() as conn:
            conn.execute("UPDATE youtube_chat_deliveries SET status='sending',response_id=NULL,completed_at=NULL,created_at=now()-interval '10 minutes' WHERE id=%s", (result['id'],))
        chat.recover()
        assert send('recovery')['status'] == 'sent'
        checks.append('restart reconciliation uses durable successful API record')
    with quota.connect() as conn:
        count = conn.execute('SELECT count(*) FROM youtube_api_requests WHERE project_id=%s AND delivery_id IS NOT NULL', (project,)).fetchone()[0]
        assert count == 5, count
    report = {'passed': True, 'run_id': run, 'project': project, 'checks': checks,
              'simulated_post_attempts': count, 'real_google_requests': 0, 'quota': quota.report(project)}
    folder = Path(os.getenv('DATA_DIR', 'data'))/'test-evidence'/'youtube-chat'/run
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
