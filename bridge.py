"""Local bridge: lets the browser extension hand downloads to the app.

Listens on 127.0.0.1 only. POST /add is accepted only from browser extensions
(Origin chrome-extension:// or moz-extension://), so ordinary websites can't use it.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9614
ALLOWED_ORIGINS = ("chrome-extension://", "moz-extension://")


def start_server(on_add, port=PORT):
    """Start the bridge. on_add(url, headers) is called from a worker thread."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _trusted(self):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            origin = self.headers.get("Origin") or ""
            return host in ("127.0.0.1", "localhost") and origin.startswith(ALLOWED_ORIGINS)

        def do_GET(self):
            if self.path == "/ping":
                self._send(200, {"app": "download-manager"})
            else:
                self._send(404, {})

        def do_POST(self):
            if self.path != "/add" or not self._trusted():
                return self._send(403, {"error": "forbidden"})
            try:
                size = int(self.headers.get("Content-Length") or 0)
                if size > 65536:
                    return self._send(413, {})
                data = json.loads(self.rfile.read(size))
                url = str(data.get("url", ""))
                if not url.lower().startswith(("http://", "https://")):
                    return self._send(400, {"error": "bad url"})
                hdrs = {}
                for key, header in (("referrer", "Referer"), ("cookie", "Cookie"), ("userAgent", "User-Agent")):
                    val = data.get(key)
                    if isinstance(val, str) and val:
                        hdrs[header] = val
                on_add(url, hdrs)
                self._send(200, {"ok": True})
            except Exception:
                self._send(400, {"error": "bad request"})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
