"""Exercise real requests/gzip handling on localhost; never contact Google."""
from contextlib import contextmanager
import gzip
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
import youtube_http as transport
import youtube_quota as quota

BODY={'snippet':{'liveChatId':'fixture','type':'textMessageEvent',
                 'textMessageDetails':{'messageText':'Zażółć — pomoc'}}}


class PostingTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                cls.calls.append((self.path,self.headers,self.rfile.read(int(self.headers['Content-Length']))))
                payload={'id':'posted','snippet':BODY['snippet']};code=200
                if self.path=='/limited':
                    code=403;payload={'error':{'message':'Posting limit','errors':[{'reason':'rateLimitExceeded','domain':'youtube.liveChat'}]}}
                elif self.path=='/redirect':code=307
                elif self.path=='/server-error':code=503;payload={}
                elif self.path=='/oversized':payload={'id':'x'*(2*1024*1024)}
                encoded=gzip.compress(json.dumps(payload,ensure_ascii=False).encode())
                self.send_response(code)
                self.send_header('Content-Encoding','gzip')
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(encoded)))
                if code==403:self.send_header('Retry-After','1800')
                if code==307:self.send_header('Location','/success')
                self.end_headers();self.wfile.write(encoded)
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):self.calls=[];type(self).calls=self.calls

    @contextmanager
    def run_post(self,path):
        endpoint=f'http://127.0.0.1:{self.server.server_port}{path}'
        with patch.object(quota,'Request',side_effect=lambda url,**kwargs:Request(endpoint,**kwargs)), \
                patch.object(quota,'reserve',return_value='request'),patch.object(quota,'finish') as finish:
            yield lambda:quota.tracked_request('fixture','liveChatMessages.insert','fake-token',
                                               params={'part':'snippet'},body=BODY,account_channel_id='channel'),finish

    def test_gzip_success_and_polish_text_roundtrip(self):
        with self.run_post('/success') as (post,finish):
            result=post()
            self.assertEqual(result['id'],'posted')
            self.assertEqual(result['snippet'],BODY['snippet'])
            finish.assert_called_once_with('request','succeeded',200,response_id='posted')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0][1]['Authorization'],'Bearer fake-token')
        self.assertEqual(json.loads(self.calls[0][2]),BODY)

    def test_compressed_rejection_keeps_provider_and_retry_after(self):
        with self.run_post('/limited') as (post,finish):
            with self.assertRaises(quota.YouTubeRequestFailed) as raised:post()
            self.assertEqual(raised.exception.reason,'rateLimitExceeded')
            self.assertTrue(raised.exception.definitive)
            self.assertEqual(finish.call_args.kwargs['provider']['retry_after_seconds'],1800)
            self.assertEqual(finish.call_args.kwargs['provider']['provider_message'],'Posting limit')
        self.assertEqual(len(self.calls),1)

    def test_redirect_and_server_error_are_never_retried(self):
        for path in ['/redirect','/server-error']:
            self.calls.clear()
            with self.run_post(path) as (post,finish):
                with self.assertRaises(quota.YouTubeRequestFailed) as raised:post()
                self.assertFalse(raised.exception.definitive)
            self.assertEqual([call[0] for call in self.calls],[path])

    def test_decompressed_response_size_is_bounded(self):
        with self.run_post('/oversized') as (post,finish):
            with self.assertRaises(quota.YouTubeRequestFailed):post()
            self.assertEqual(finish.call_args.args[1],'unknown')
        self.assertEqual(len(self.calls),1)

    def test_post_settings_match_diagnostic_no_redirect_or_retry(self):
        request=Request('https://www.googleapis.com/youtube/v3/liveChat/messages?part=snippet',
                        data=json.dumps(BODY).encode(),headers={'Authorization':'Bearer fake'},method='POST')
        with patch.object(transport.requests,'Session') as session:
            session.return_value.__enter__.return_value.post.side_effect=requests.Timeout('fake')
            with self.assertRaises(requests.Timeout):
                with transport.open_request(request):pass
            client=session.return_value.__enter__.return_value
            client.post.assert_called_once()
            self.assertFalse(client.post.call_args.kwargs['allow_redirects'])
            self.assertEqual(client.post.call_args.kwargs['timeout'],(10,30))
            self.assertTrue(client.post.call_args.kwargs['stream'])
            self.assertEqual(client.mount.call_args.args[1].max_retries.total,0)


if __name__=='__main__':unittest.main()
