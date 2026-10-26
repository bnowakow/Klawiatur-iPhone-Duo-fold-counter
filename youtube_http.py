"""Single-attempt YouTube HTTP; posting uses the independently verified client."""
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import requests
from requests.adapters import HTTPAdapter


@contextmanager
def open_request(request, timeout=30):
    if request.get_method() != 'POST':
        with urlopen(request, timeout=timeout) as response:
            yield response
        return
    # Match the successful diagnostic POST. No redirect, refresh or HTTP retry
    # may publish the same message twice. Authentication is supplied by caller.
    with requests.Session() as session:
        session.mount('https://', HTTPAdapter(max_retries=0))
        with session.post(request.full_url, data=request.data,
                          headers=dict(request.header_items()), timeout=(10, timeout),
                          allow_redirects=False, stream=True) as response:
            if response.status_code >= 300:
                raw = response.raw.read(65536, decode_content=True)
                headers = {'Retry-After': response.headers['Retry-After']} if 'Retry-After' in response.headers else {}
                raise HTTPError(request.full_url, response.status_code, 'YouTube request failed', headers, BytesIO(raw))
            # requests.raw is compressed by default: explicitly decode before
            # parsing, while retaining the caller's decompressed size bound.
            yield SimpleNamespace(status=response.status_code,
                                  read=lambda size: response.raw.read(size, decode_content=True))
