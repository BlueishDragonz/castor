"""Browser-origin protection at the outer ASGI boundary (including NiceGUI WS)."""
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


def origin_tuple(value):
    try:
        url = urlsplit(value)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            return None
        return url.scheme, url.hostname.lower(), url.port or (443 if url.scheme == 'https' else 80)
    except ValueError:
        return None


class BrowserOriginMiddleware:
    """Fail closed on cross-origin browser writes, retain cookie-free native clients.

    Origin/Referer checks are CSRF defenses, not authentication. Auth remains at
    route dependencies. No Authorization-header presence exemption is used.
    """
    def __init__(self, app, allowed_origins=()):
        self.app = app
        self.allowed = {o for v in allowed_origins if (o := origin_tuple(v))}

    async def __call__(self, scope, receive, send):
        kind = scope['type']
        if kind not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        websocket = kind == 'websocket'
        unsafe = websocket or scope.get('method', 'GET') not in ('GET', 'HEAD', 'OPTIONS')
        # Engine.IO polling can carry the browser session; guard its handshake too.
        unsafe = unsafe or '/socket.io' in scope.get('path', '')
        if unsafe:
            scheme = 'https' if scope.get('scheme') in ('https', 'wss') else 'http'
            target = origin_tuple(scheme + '://' + headers.get('host', ''))
            allowed = self.allowed or {target}
            origin = headers.get('origin')
            referer = headers.get('referer')
            supplied = origin if origin is not None else referer
            rejected = headers.get('sec-fetch-site') == 'cross-site'
            if supplied is not None:
                rejected |= origin_tuple(supplied) not in allowed
            elif headers.get('cookie'):
                rejected = True
            if rejected:
                if websocket:
                    await send({'type': 'websocket.close', 'code': 1008})
                else:
                    await JSONResponse({'detail': 'Browser origin not allowed'}, status_code=403)(scope, receive, send)
                return
        # Record outcomes for reset requests and HTTP reset failures without bodies.
        async def audit_send(message):
            if kind == 'http' and message['type'] == 'http.response.start':
                path = scope.get('path', '')
                code = message['status']
                event = {'/auth/forgot-password': 'password_reset_request',
                         '/auth/logout': 'logout'}.get(path)
                if path == '/auth/reset-password' and code >= 400:
                    event = 'password_reset'
                if event and scope.get('method') == 'POST':
                    from beaverhabits.app.audit import record
                    await record(event, 'success' if code < 400 else 'failure')
            await send(message)
        await self.app(scope, receive, audit_send)
