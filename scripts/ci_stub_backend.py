"""Stub backend for the CI smoke job.

The /api/v1/tokens BFF route calls backendFetch() *before* it builds its
redirect. With nothing listening on 8085 that fetch throws, Astro returns 500,
and there is no Location header — so the F22 open-redirect gate has nothing
to assert and fails with "no Location header" for reasons that have nothing
to do with the redirect check itself.

A 401 stub is enough: the route only needs the fetch to resolve. It then emits
its 303 + Location, which is precisely what the gate asserts. No real backend,
no real data, and the route's own session handling is still exercised because
the route does not short-circuit before calling the backend.
"""
import http.server
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8085


class Handler(http.server.BaseHTTPRequestHandler):
    def _respond(self):
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"detail":"stub backend"}')

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _respond

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
