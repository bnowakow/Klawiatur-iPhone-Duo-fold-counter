from datetime import datetime, timezone
import json
import io
from contextlib import redirect_stdout
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, MagicMock

import stream_lifecycle as lifecycle
import supervisor
import youtube_chat as chat
import youtube_bot as bot


class StreamLifecycleTests(unittest.TestCase):
    def test_bot_replies_after_video_offline_until_explicit_chat_closure(self):
        first=MagicMock()
        first.__enter__.return_value.execute.return_value.fetchone.return_value=None
        leader=MagicMock()
        leader.__enter__.return_value.execute.return_value.fetchone.return_value=(True,)
        stop=threading.Event()
        shutdown=MagicMock()
        target={'chat_id':'chat','channel':{'id':'channel','title':'fixture'}}
        message={'id':'after-video','text':'!pomoc'}
        def responses(*args):
            yield {'offline_at':'2026-10-26T10:00:00Z','items':[],'next_page_token':'first'}
            yield {'offline_at':'','items':[message],'next_page_token':'second'}
            raise lifecycle.ChatClosed('Chat no longer accepts replies')
        with patch.dict('os.environ',{'YOUTUBE_PROJECT_ID':'fixture','YOUTUBE_CHAT_ENABLED':'true'}), \
                patch.object(bot,'initialize'), patch.object(bot.quota,'connect',side_effect=[first,leader]), \
                patch.object(bot.auth,'locked',return_value=MagicMock()), patch.object(bot.auth,'stored',return_value={}), \
                patch('youtube_stint_notifications.start',return_value=MagicMock()), \
                patch.object(bot,'start_delayed_commands',return_value=MagicMock()), \
                patch.object(bot.threading,'Event',side_effect=[stop,shutdown]), patch.object(bot.signal,'signal'), \
                patch.object(bot.chat,'context',return_value=target), \
                patch.object(bot,'remember_state',return_value=(datetime.now(timezone.utc),None)), \
                patch.object(bot,'reconcile'), patch.object(bot,'heartbeat'), \
                patch.object(bot,'batches',side_effect=responses), patch.object(bot,'video_finished') as finished, \
                patch.object(bot,'chat_finished') as closed, \
                patch.object(bot,'process_message',return_value={'status':'sent'}) as reply, redirect_stdout(io.StringIO()):
            bot.run()
        reply.assert_called_once()
        self.assertEqual(reply.call_args.args[-1],message)
        finished.assert_called_once()
        closed.assert_called_once()
        shutdown.wait.assert_called_once()

    def test_only_positive_broadcast_end_metadata_is_terminal(self):
        for status in ('was_live','post_live'):
            result=SimpleNamespace(stdout=json.dumps({'is_live':False,'live_status':status,'id':'vaiHWiExDXg'}))
            with patch('supervisor.subprocess.run',return_value=result) as run:
                with self.assertRaises(lifecycle.StreamEnded):
                    supervisor.capture('url',Path('/unused'))
            self.assertEqual(run.call_count,1)
        for status in ('is_upcoming','not_live',None):
            result=SimpleNamespace(stdout=json.dumps({'is_live':False,'live_status':status}))
            with patch('supervisor.subprocess.run',return_value=result):
                with self.assertRaises(RuntimeError) as caught:
                    supervisor.capture('url',Path('/unused'))
                self.assertNotIsInstance(caught.exception,lifecycle.StreamEnded)

    def test_finish_retains_counter_and_is_idempotent(self):
        stamp='2026-10-26T10:00:00+00:00'
        current={'id':'session','detected_at':stamp,'last_counter':75010,
                 'last_counter_at':stamp,'last_increase_at':stamp}
        state={'counter':{'anchor':{'value':75010}},'presenters':{'current':current}}
        obs={'id':'end','observed_at':'2026-10-26T10:01:00+00:00','details':{}}
        result=lifecycle.finish_state(state,obs,{'source':'youtube.actualEndTime'})
        closed=obs['details']['presenter_session_updates'][0]
        self.assertEqual(closed['last_counter'],75010)
        self.assertEqual(closed['ended_at'],stamp)
        self.assertFalse(closed['end_evidence']['exact_stop_verified'])
        self.assertIn('current',state['presenters'])
        self.assertNotIn('current',result['presenters'])
        later={'id':'again','observed_at':stamp,'details':{}}
        lifecycle.finish_state(result,later,{'source':'youtube.actualEndTime'})
        self.assertNotIn('presenter_session_updates',later['details'])

    def test_existing_folding_pause_is_not_closed_twice(self):
        state={'presenters':{'activity_pause':{'counter':75000}}}
        obs={'id':'end','observed_at':'2026-10-26T10:01:00+00:00','details':{}}
        result=lifecycle.finish_state(state,obs,{'source':'yt_dlp.live_status'})
        self.assertNotIn('presenter_session_updates',obs['details'])
        self.assertEqual(result['presenters'],state['presenters'])

    def test_ended_video_reuses_known_chat_instead_of_stopping_replies(self):
        connection=MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.side_effect=[('youtube_bot_state',),('known-chat',)]
        details={'actualEndTime':'2026-10-26T10:00:00Z'}
        with patch.object(chat.auth,'token',return_value='private'), \
                patch.object(chat,'identify',return_value={'id':'channel'}), \
                patch.object(chat.quota,'tracked_request',return_value={'items':[{'liveStreamingDetails':details}]}), \
                patch.object(chat.quota,'connect',return_value=connection):
            result=chat.context('project','https://youtu.be/vaiHWiExDXg')
        self.assertEqual(result['chat_id'],'known-chat')
        self.assertEqual(result['actual_ended_at'],details['actualEndTime'])

    def test_missing_video_is_not_silently_a_confirmed_broadcast_end(self):
        with patch.object(chat.auth,'token',return_value='private'), \
                patch.object(chat,'identify',return_value={'id':'channel'}), \
                patch.object(chat.quota,'tracked_request',return_value={'items':[]}):
            with self.assertRaises(ValueError):
                chat.context('project','https://youtu.be/vaiHWiExDXg')
