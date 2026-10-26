import threading
import unittest
from unittest.mock import MagicMock, patch
import grpc
import youtube_stream as stream


class StreamError(grpc.RpcError):
    def __init__(self, code):
        self.status = code

    def code(self):
        return self.status

    def details(self):
        return 'Precondition check failed'


class YouTubeStreamTests(unittest.TestCase):
    def test_generic_closed_chat_error_is_terminal_but_network_failures_are_not(self):
        for code, expected in [(grpc.StatusCode.FAILED_PRECONDITION, 'liveChatDisabledOrEnded'),
                                (grpc.StatusCode.UNAVAILABLE, 'grpc_unavailable')]:
            with self.subTest(code=code):
                call = MagicMock()
                call.__iter__.side_effect = StreamError(code)
                stub = MagicMock()
                stub.StreamList.return_value = call
                with patch.object(stream.auth, 'locked', return_value=MagicMock()), \
                        patch.object(stream.auth, 'token', return_value='fixture'), \
                        patch.object(stream.quota, 'reserve', return_value='request'), \
                        patch.object(stream.quota, 'finish') as finish, \
                        patch('grpc.secure_channel', return_value=MagicMock()), \
                        patch('youtube_stream_pb2_grpc.V3DataLiveChatMessageServiceStub', return_value=stub):
                    with self.assertRaises(stream.quota.YouTubeRequestFailed) as caught:
                        list(stream.batches('project','chat',None,threading.Event()))
                self.assertEqual(caught.exception.reason, expected)
                self.assertEqual(finish.call_args.kwargs['error_reason'], expected)
                call.cancel.assert_called_once()
