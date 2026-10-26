"""Exercise real PostgreSQL accounting without sending any Google requests."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import os
import uuid
import youtube_quota as q


def main():
    run = str(uuid.uuid4())
    project = 'integration:youtube:' + run
    account = 'integration:account:' + run
    now = datetime.now(timezone.utc)
    q.configure(project, 45)
    first = q.reserve(project, 'liveChatMessages.insert', account, now)
    q.finish(first, 'failed', 403, 'test_failure')
    q.finish(first, 'succeeded', 200)  # completion must be idempotent
    q.reserve(project, 'liveChatMessages.insert', account, now)
    q.reserve(project, 'videos.list', now=now)
    try:
        q.reserve(project, 'liveChatMessages.list', now=now)
        raise AssertionError('Exceeded budget was accepted')
    except q.QuotaExceeded:
        pass
    report = q.report(project, now)
    assert report['estimated_used_units'] == 41 and report['failed'] == 1 and report['uncertain'] == 2
    assert q.report(project, now+timedelta(days=1))['estimated_used_units'] == 0
    concurrent = project + ':concurrent'
    q.configure(concurrent, 20)
    def attempt(_):
        try:
            q.reserve(concurrent, 'liveChatMessages.insert', account+'c', now)
            return True
        except q.QuotaExceeded:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(attempt, range(2))) == 1
    rate = project + ':rate'
    q.configure(rate, 10000)
    for _ in range(11):
        q.reserve(rate, 'liveChatMessages.insert', account+'r', now)
    try:
        q.reserve(rate, 'liveChatMessages.insert', account+'r', now)
        raise AssertionError('12th attempt accepted')
    except q.ChatRateLimited:
        pass
    q.reserve(rate, 'liveChatMessages.insert', account+'r', now+timedelta(seconds=31))
    result = {'passed': True, 'test_project': project, 'checks': ['budget', 'failed and uncertain counted', 'idempotent completion', 'Pacific daily rollover', 'concurrent reservation', 'account posting guard'], 'real_google_requests': 0, 'report': report}
    directory = Path(os.getenv('DATA_DIR', 'data'))/'test-evidence'/'youtube-quota'/run
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
