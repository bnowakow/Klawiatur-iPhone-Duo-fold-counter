"""Separate Sheets OAuth credentials; reuses the existing Google client and callback."""
from contextlib import contextmanager
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
REDIRECT_URI = 'http://localhost:8765/oauth/callback'
CONSENT_TIMEOUT_SECONDS = 1800


class AuthorizationRequired(RuntimeError):
    pass


def directory():
    path = Path(os.getenv('SHEETS_AUTH_DIR', '/secrets/sheets'))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


@contextmanager
def locked():
    """Serialize Sheets refresh, connect and exports across local containers."""
    with open(directory()/'credentials.lock', 'a', opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def save(value):
    folder = directory()
    fd, name = tempfile.mkstemp(prefix='.credentials-', dir=folder)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, folder/'credentials.json')
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stored():
    path = directory()/'credentials.json'
    if not path.exists():
        raise AuthorizationRequired('Connect your Google account with make sheets-auth')
    return json.loads(path.read_text())


def client_config(project_id):
    value = json.loads(Path(os.getenv('GOOGLE_OAUTH_CLIENT_FILE', '/secrets/youtube/client.json')).read_text())
    web = value.get('web', {})
    if web.get('project_id') != project_id:
        raise ValueError('OAuth client project_id must match YOUTUBE_PROJECT_ID')
    if REDIRECT_URI not in web.get('redirect_uris', []):
        raise ValueError('Register the exact callback URI: '+REDIRECT_URI)
    if (web.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or
            web.get('token_uri') != 'https://oauth2.googleapis.com/token'):
        raise ValueError('Expected a Google Web application OAuth client JSON')
    return value


def token(project_id):
    """Caller holds locked(); token is never returned by status or printed."""
    value = stored()
    if value.get('project_id') != project_id:
        raise AuthorizationRequired('Connected credentials belong to another Cloud project')
    if SCOPE not in value['credentials'].get('scopes', []):
        raise AuthorizationRequired('Sheets permission is missing; run make sheets-auth')
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from google.auth.exceptions import RefreshError
    credentials = Credentials.from_authorized_user_info(value['credentials'], scopes=[SCOPE])
    if not credentials.valid:
        try:
            credentials.refresh(GoogleRequest())
        except RefreshError:
            raise AuthorizationRequired('Google authorization expired or was revoked; reconnect your account') from None
        value['credentials'] = json.loads(credentials.to_json())
        save(value)
    return credentials.token


def callback_code(path, host, state):
    parsed = urlsplit(path)
    query = parse_qs(parsed.query)
    if host != 'localhost:8765' or parsed.path != '/oauth/callback':
        raise ValueError('Unexpected callback address')
    received = query.get('state', [])
    if len(received) != 1 or not secrets.compare_digest(received[0], state):
        raise ValueError('OAuth state mismatch')
    if 'error' in query:
        raise AuthorizationRequired('Google consent was declined')
    codes = query.get('code', [])
    if len(codes) != 1 or not codes[0]:
        raise ValueError('OAuth callback has no authorization code')
    return codes[0]


def authorize(project_id):
    """Caller holds locked(). Bind only via a loopback-published Compose port."""
    from google_auth_oauthlib.flow import Flow
    if (directory()/'credentials.json').exists():
        raise ValueError('An account is already connected; disconnect it before replacing it')
    flow = Flow.from_client_config(client_config(project_id), scopes=[SCOPE],
                                   redirect_uri=REDIRECT_URI, autogenerate_code_verifier=True)
    url, state = flow.authorization_url(access_type='offline', prompt='consent', include_granted_scopes='false')
    outcome = {}
    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log callback query strings, codes or tokens.
        def do_GET(self):
            try:
                code = callback_code(self.path, self.headers.get('Host'), state)
                outcome['code'] = code
                status, message = 200, 'Authorization received. Return to the terminal.'
            except AuthorizationRequired:
                outcome['denied'] = True
                status, message = 400, 'Authorization declined. Return to the terminal.'
            except ValueError:
                status, message = 400, 'Invalid authorization callback.'
            self.send_response(status)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            self.wfile.write(message.encode())
    class CallbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(5)
            return connection, address
    with CallbackServer(('0.0.0.0', 8765), Callback) as server:
        server.timeout = 1
        print('Open this URL in your browser:\n'+url, flush=True)
        deadline = time.monotonic()+CONSENT_TIMEOUT_SECONDS
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    if 'code' not in outcome:
        raise AuthorizationRequired('Authorization declined or timed out; run connect again')
    try:
        flow.fetch_token(code=outcome['code'])
    except Exception:
        raise AuthorizationRequired('Google token exchange failed; run connect again') from None
    granted = set(flow.credentials.granted_scopes or flow.credentials.scopes or [])
    if SCOPE not in granted:
        raise AuthorizationRequired('Sheets permission was not granted; run connect again')
    if not flow.credentials.refresh_token:
        raise AuthorizationRequired('No offline refresh token received; revoke the grant and reconnect')
    save({'project_id': project_id, 'credentials': json.loads(flow.credentials.to_json())})
    return {'connected': True, 'scope': SCOPE}


def disconnect():
    path = directory() / 'credentials.json'
    path.unlink(missing_ok=True)
    return {'connected': False, 'removed_locally': True}
