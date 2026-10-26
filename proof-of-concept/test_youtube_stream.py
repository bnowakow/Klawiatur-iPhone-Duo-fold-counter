from contextlib import nullcontext
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_stream as stream


class StreamTests(unittest.TestCase):
    def test_official_proto_fields_resume_and_quota_accounting(self):
        import youtube_stream_pb2 as pb
        import youtube_stream_pb2_grpc as rpc
        response = pb.LiveChatMessageListResponse(next_page_token='next')
        item = response.items.add(id='incoming')
        item.snippet.type = 1
        item.snippet.author_channel_id = 'author'
        item.snippet.published_at = '2026-10-26T07:00:00Z'
        item.snippet.text_message_details.message_text = '!ile'
        call = MagicMock()
        call.__iter__.return_value = iter([response])
        stub = MagicMock()
        stub.StreamList.return_value = call
        with patch.object(stream.auth,'locked',return_value=nullcontext()), patch.object(stream.auth,'token',return_value='fake-token'), patch.object(stream.quota,'reserve',return_value='request') as reserve, patch.object(stream.quota,'finish') as finish, patch('grpc.secure_channel'), patch.object(rpc,'V3DataLiveChatMessageServiceStub',return_value=stub):
            result = list(stream.batches('project','chat','cursor',threading.Event()))
        reserve.assert_called_once_with('project','liveChatMessages.streamList')
        finish.assert_called_once_with('request','succeeded',200)
        self.assertEqual(stub.StreamList.call_args.args[0].page_token,'cursor')
        self.assertEqual(result[0]['items'][0]['text'],'!ile')
        self.assertEqual(result[0]['next_page_token'],'next')
        call.cancel.assert_called()


if __name__=='__main__':
    unittest.main()
