"""Quota-accounted official gRPC live chat stream; no polling loop."""
import threading
import youtube_auth as auth
import youtube_quota as quota


def batches(project, chat_id, page_token, stop, duration=900):
    import grpc
    import youtube_stream_pb2 as pb
    import youtube_stream_pb2_grpc as rpc
    with auth.locked():
        access_token = auth.token(project)
    request_id = quota.reserve(project, 'liveChatMessages.streamList')
    received = False
    closed = threading.Event()
    call = None
    try:
        with grpc.secure_channel('dns:///youtube.googleapis.com:443', grpc.ssl_channel_credentials()) as channel:
            stub = rpc.V3DataLiveChatMessageServiceStub(channel)
            request = pb.LiveChatMessageListRequest(live_chat_id=chat_id, part=['id','snippet'], max_results=200)
            if page_token:
                request.page_token = page_token
            call = stub.StreamList(request, metadata=(('authorization', 'Bearer '+access_token),), timeout=duration)
            def cancel_when_stopped():
                while not closed.wait(0.5):
                    if stop.is_set():
                        call.cancel()
                        return
            threading.Thread(target=cancel_when_stopped, daemon=True).start()
            for response in call:
                if not received:
                    quota.finish(request_id, 'succeeded', 200)
                    received = True
                yield {'next_page_token': response.next_page_token, 'offline_at': response.offline_at,
                       'items': [{'id': item.id, 'type': item.snippet.type,
                                  'author_channel_id': item.snippet.author_channel_id,
                                  'published_at': item.snippet.published_at,
                                  'text': item.snippet.text_message_details.message_text}
                                 for item in response.items]}
    except grpc.RpcError as error:
        code = error.code()
        reason='grpc_'+code.name.lower()
        if code == grpc.StatusCode.FAILED_PRECONDITION:
            # YouTube documents this code as disabled OR ended chat. The
            # generic "Precondition check failed" text cannot distinguish them.
            reason='liveChatDisabledOrEnded'
        if not received:
            quota.finish(request_id, 'unknown' if code in (grpc.StatusCode.DEADLINE_EXCEEDED, grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.CANCELLED)
                         else 'failed', error_reason=reason)
        if stop.is_set() or (received and code == grpc.StatusCode.DEADLINE_EXCEEDED):
            return
        # Provider details may contain private request data; expose only the status code.
        raise quota.YouTubeRequestFailed('Live chat stream ended: '+code.name, reason=reason) from None
    except GeneratorExit:
        if not received:
            quota.finish(request_id, 'unknown', error_reason='stream_closed')
        raise
    except Exception:
        if not received:
            quota.finish(request_id, 'unknown', error_reason='stream_connection_failure')
        raise
    finally:
        closed.set()
        if call is not None:
            call.cancel()
